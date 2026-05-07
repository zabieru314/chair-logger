"""
センサー監視コア

【絶対厳守の設計方針】
- ポーリング厳禁。`subprocess.run` で繰り返し呼ぶのは熱暴走の原因になるためNG。
- `termux-sensor -s gravity -d 1000` を `subprocess.Popen` で1回だけ起動し、
  標準出力をストリームとして1行ずつ非同期に読み取る。
- 状態が変化してから DEBOUNCE_DELAY_SEC 秒の間その状態が継続した場合のみ
  「確定」とみなす（チャタリング対策）。
- 温度監視スレッドが「休止指示」を出している間は、Popen を一旦終了させ、
  指定秒数 sleep してから再起動する（温度を下げる時間を確保）。
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from src.db import models as db_models
from src.utils import hardware, notifier

logger = logging.getLogger(__name__)


# ステータス定数
STATUS_SEATED = "seated"
STATUS_LEFT = "left"


@dataclass
class SensorConfig:
    db_path: str
    webhook_url: Optional[str]
    z_threshold: float
    debounce_delay_sec: float
    max_temp_celsius: float
    cooldown_sleep_sec: int
    temp_check_interval_sec: float
    sensor_interval_ms: int


class SensorMonitor:
    """
    重力センサーをストリームで読み続け、Debounce後に状態確定するモニター。

    状態機械:
        confirmed_state    : 直近で確定済みの状態（初期値 None）
        candidate_state    : 観測中の遷移候補（None の時は遷移待ちでない）
        candidate_since    : candidate_state を最初に観測した時刻

    確定条件:
        candidate_state が DEBOUNCE_DELAY_SEC 以上継続して観測されたら
        confirmed_state に昇格させ、DBへ書き込み + Webhook通知。
    """

    # termux-sensor の values 配列から数値を抽出する正規表現
    # 出力例: "values" : [ 0.12, 9.80, 0.34 ]  ← Z値は3番目
    _VALUES_LINE_RE = re.compile(r'"values"\s*:\s*\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)')

    def __init__(self, config: SensorConfig):
        self.config = config

        # 確定状態 / 候補状態
        self._confirmed_state: Optional[str] = None
        self._candidate_state: Optional[str] = None
        self._candidate_since: Optional[float] = None
        self._latest_z: Optional[float] = None

        # 制御フラグ
        self._stop_event = threading.Event()
        self._cooldown_event = threading.Event()  # set されている間は休止
        self._state_lock = threading.Lock()

        # サブスレッド
        self._sensor_thread: Optional[threading.Thread] = None
        self._temp_thread: Optional[threading.Thread] = None

        # Popen ハンドル
        self._proc: Optional[subprocess.Popen] = None

    # ------------------------------------------------------------------
    # 公開API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """センサー監視スレッドと温度監視スレッドを起動する。"""
        if self._sensor_thread and self._sensor_thread.is_alive():
            logger.warning("SensorMonitor は既に起動済みです。")
            return

        self._stop_event.clear()
        self._cooldown_event.clear()

        self._sensor_thread = threading.Thread(
            target=self._sensor_loop, name="SensorLoop", daemon=True
        )
        self._temp_thread = threading.Thread(
            target=self._temp_loop, name="TempMonitor", daemon=True
        )

        self._sensor_thread.start()
        self._temp_thread.start()
        logger.info("SensorMonitor 起動完了")

    def stop(self) -> None:
        """監視を停止する。"""
        logger.info("SensorMonitor 停止要求を受信")
        self._stop_event.set()
        self._terminate_proc()

    def get_current_state(self) -> dict:
        """現在の状態を辞書で返す（Web側から参照される）。"""
        with self._state_lock:
            return {
                "confirmed_state": self._confirmed_state,
                "candidate_state": self._candidate_state,
                "candidate_since": self._candidate_since,
                "latest_z": self._latest_z,
                "is_cooldown": self._cooldown_event.is_set(),
            }

    # ------------------------------------------------------------------
    # 温度監視ループ
    # ------------------------------------------------------------------

    def _temp_loop(self) -> None:
        """
        定期的にバッテリー温度をチェックし、上限超過時は休止フラグを立てる。
        termux-battery-status が使えない環境（Termux:API 未インストール等）では即終了する。
        """
        if not hardware.is_termux_available():
            logger.info("termux-battery-status が見つかりません。温度監視を無効化します。")
            return

        cfg = self.config
        while not self._stop_event.is_set():
            try:
                over, temp = hardware.is_overheating(cfg.max_temp_celsius)
                if over:
                    logger.warning(
                        f"温度上限超過: {temp}℃ >= {cfg.max_temp_celsius}℃。"
                        f"{cfg.cooldown_sleep_sec}秒の休止に入ります。"
                    )
                    notifier.notify_overheat(cfg.webhook_url, temp or 0.0, cfg.cooldown_sleep_sec)

                    # 休止フラグON → センサーループ側がPopenを終了させる
                    self._cooldown_event.set()
                    self._terminate_proc()

                    # 休止中も停止指示はチェックする
                    waited = 0.0
                    step = 1.0
                    while waited < cfg.cooldown_sleep_sec and not self._stop_event.is_set():
                        time.sleep(step)
                        waited += step

                    # 復帰
                    self._cooldown_event.clear()
                    notifier.notify_resume(cfg.webhook_url, hardware.get_battery_temperature())
                    logger.info("休止終了。センサー監視を再開します。")
                else:
                    if temp is not None:
                        logger.debug(f"バッテリー温度: {temp:.1f}℃")
            except Exception as e:
                logger.exception(f"温度監視ループで例外: {e}")

            # チェック間隔
            self._stop_event.wait(cfg.temp_check_interval_sec)

    # ------------------------------------------------------------------
    # センサーループ（Popenでストリーム読み取り）
    # ------------------------------------------------------------------

    def _sensor_loop(self) -> None:
        """
        termux-sensor をPopenで起動し、出力を1行ずつ読み取って状態を更新する。
        休止中はPopenを起動しない。停止指示で抜ける。
        """
        cfg = self.config
        while not self._stop_event.is_set():
            # 休止中はPopenを起動しない
            if self._cooldown_event.is_set():
                time.sleep(1.0)
                continue

            try:
                self._run_sensor_once()
            except Exception as e:
                logger.exception(f"センサーループで例外、5秒後にリトライ: {e}")
                time.sleep(5.0)

    def _run_sensor_once(self) -> None:
        """termux-sensor を1回起動して、終了するまで読み続ける。"""
        cfg = self.config
        cmd = ["termux-sensor", "-s", "gravity", "-d", str(cfg.sensor_interval_ms)]

        logger.info(f"termux-sensor を起動: {' '.join(cmd)}")
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,  # バイナリモード（子プロセスのバッファリングをバイパス）
            )
        except FileNotFoundError:
            logger.error(
                "termux-sensor コマンドが見つかりません。Termux:API をインストールしてください。"
            )
            time.sleep(30.0)
            return
        except Exception as e:
            logger.exception(f"termux-sensor 起動失敗: {e}")
            time.sleep(5.0)
            return

        # JSONブロックを蓄積するバッファ（termux-sensor は複数行JSONを吐く）
        buf: list[str] = []
        depth = 0
        line_count = 0

        try:
            assert self._proc.stdout is not None
            for raw in self._proc.stdout:
                if self._stop_event.is_set() or self._cooldown_event.is_set():
                    logger.info("停止/休止指示を検出、Popenを終了します。")
                    break

                line = raw.decode("utf-8", errors="replace")
                line_count += 1

                # 最初の数行と定期的にデバッグログを出して疎通確認
                if line_count <= 10 or line_count % 50 == 0:
                    logger.info(f"[センサー生出力 L{line_count}] {line.rstrip()}")

                # values配列の1行完結パターン: "values" : [ x, y, z ]
                m = self._VALUES_LINE_RE.search(line)
                if m:
                    try:
                        z = float(m.group(3))
                        logger.debug(f"values正規表現でZ値取得: {z:.3f}")
                        self._handle_z(z)
                    except ValueError:
                        pass

                # JSONブロックを蓄積して複数行形式にも対応
                buf.append(line)
                depth += line.count("{") - line.count("}")
                if depth <= 0 and buf:
                    block = "".join(buf).strip()
                    buf.clear()
                    depth = 0
                    if block:
                        self._handle_json_block(block)

        except Exception as e:
            logger.exception(f"センサー出力読み取り中に例外: {e}")
        finally:
            self._terminate_proc()

    def _handle_json_block(self, block: str) -> None:
        """JSONブロックが切り出せたら gravity の z 値を抽出して扱う。"""
        try:
            data = json.loads(block)
        except json.JSONDecodeError as e:
            logger.debug(f"JSONパース失敗: {e} | block先頭50字: {block[:50]!r}")
            return

        if not isinstance(data, dict):
            logger.debug(f"JSON最上位がdictでない: {type(data)}")
            return

        # termux-sensor の出力形式: {"gravity": {"values": [x, y, z]}} など
        # キー名がスペース入り（"gravity "）の場合も考慮
        sensor = None
        for key in data:
            if "gravity" in key.lower():
                sensor = data[key]
                break

        if not isinstance(sensor, dict):
            logger.debug(f"gravityキーが見つからない。キー一覧: {list(data.keys())}")
            return

        values = sensor.get("values")
        if isinstance(values, list) and len(values) >= 3:
            try:
                z = float(values[2])
                logger.debug(f"JSONブロックでZ値取得: {z:.3f}")
                self._handle_z(z)
            except (TypeError, ValueError) as e:
                logger.debug(f"Z値変換失敗: {e}")
                return
        else:
            logger.debug(f"valuesが不正: {values!r}")

    # ------------------------------------------------------------------
    # 状態機械（Debounce）
    # ------------------------------------------------------------------

    def _handle_z(self, z: float) -> None:
        """1サンプル分のZ値を受けて、Debounce状態機械を回す。"""
        cfg = self.config

        # Z軸が閾値以上 → 椅子が立っている = 着席中
        # Z軸が閾値未満 → 椅子が傾いた / 持ち上げられた = 離席中
        observed = STATUS_SEATED if z >= cfg.z_threshold else STATUS_LEFT
        now = time.monotonic()

        with self._state_lock:
            self._latest_z = z

            # 確定状態がまだ無い場合は即時確定（初回起動時）
            if self._confirmed_state is None:
                # 初回も Debounce を一応かける（誤判定防止）
                if self._candidate_state is None:
                    self._candidate_state = observed
                    self._candidate_since = now
                    return
                if observed != self._candidate_state:
                    self._candidate_state = observed
                    self._candidate_since = now
                    return
                if (now - (self._candidate_since or now)) >= cfg.debounce_delay_sec:
                    self._commit_state(observed, z)
                return

            # 確定状態と同じならリセット
            if observed == self._confirmed_state:
                if self._candidate_state is not None:
                    logger.debug(f"候補状態 {self._candidate_state} をキャンセル（戻った）")
                self._candidate_state = None
                self._candidate_since = None
                return

            # 確定状態と異なる観測 → 候補として記録
            if self._candidate_state != observed:
                logger.debug(f"候補状態を更新: {observed} (z={z:.2f})")
                self._candidate_state = observed
                self._candidate_since = now
                return

            # 候補が継続中。閾値時間を満たしたら確定へ昇格
            elapsed = now - (self._candidate_since or now)
            if elapsed >= cfg.debounce_delay_sec:
                self._commit_state(observed, z)

    def _commit_state(self, new_state: str, z: float) -> None:
        """確定処理。state_lock 保有中の前提。"""
        cfg = self.config
        prev = self._confirmed_state
        self._confirmed_state = new_state
        self._candidate_state = None
        self._candidate_since = None

        logger.info(f"状態確定: {prev} -> {new_state} (z={z:.2f})")

        # DB書き込み
        ok = db_models.insert_status(
            cfg.db_path,
            new_state,
            z_value=z,
            note=None,
        )
        if not ok:
            logger.error("DB書き込みに失敗しました（通知は継続）")

        # Webhook通知（送信失敗は無視）
        try:
            if new_state == STATUS_SEATED:
                notifier.notify_seated(cfg.webhook_url)
            else:
                notifier.notify_left(cfg.webhook_url)
        except Exception as e:
            logger.exception(f"通知ハンドラで例外: {e}")

    # ------------------------------------------------------------------
    # 後処理
    # ------------------------------------------------------------------

    def _terminate_proc(self) -> None:
        """Popen を安全に終了させる。"""
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception as e:
            logger.exception(f"Popen 終了処理で例外: {e}")
