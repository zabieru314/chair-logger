"""
センサー監視コア

【設計方針】
- SENSOR_POLL_INTERVAL_SEC ごとに termux-sensor -s gravity -n 1 で1点取得
- 前回値との差分 |ΔX|+|ΔY|+|ΔZ| を計算
- 差分 >= variance_threshold → 着席（動きあり）
- 差分 <  variance_threshold → 離席候補（DEBOUNCE_LEFT_SEC 継続で確定）
- 着席候補は DEBOUNCE_DELAY_SEC 継続で確定

【停止方法】
- stop_event を set するだけ。-n 1 なのでプロセスは自然終了するため
  SIGINT/pkill は不要。
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from src.db import models as db_models
from src.utils import hardware, notifier

logger = logging.getLogger(__name__)

STATUS_SEATED = "seated"
STATUS_LEFT = "left"

STATUS_LOG_INTERVAL_SEC = 30.0  # ポーリング間隔と合わせて毎回ログ出力


_BATTERY_ALERT_THRESHOLDS = [20, 15, 10]  # 残量アラート閾値（%）


@dataclass
class SensorConfig:
    db_path: str
    webhook_url: Optional[str]
    variance_threshold: float
    debounce_delay_sec: float       # 着席確定までの秒数
    debounce_left_sec: float        # 離席確定までの秒数（運用時は 300）
    max_temp_celsius: float
    cooldown_sleep_sec: int
    temp_check_interval_sec: float
    sensor_poll_interval_sec: int = 30  # ポーリング間隔（秒）
    summary_webhook_url: Optional[str] = None  # バッテリーアラート送信先


class SensorMonitor:
    """
    重力センサーを SENSOR_POLL_INTERVAL_SEC ごとに 1 点取得し、
    前回値との差分で着席/離席を判定するモニター。

    状態機械:
        confirmed_state : 直近で確定済みの状態（初期値 None）
        candidate_state : 観測中の遷移候補
        candidate_since : candidate_state を最初に観測した時刻

    確定条件:
        着席候補が debounce_delay_sec 継続 → 着席確定
        離席候補が debounce_left_sec  継続 → 離席確定
    """

    def __init__(self, config: SensorConfig):
        self.config = config

        self._confirmed_state: Optional[str] = None
        self._candidate_state: Optional[str] = None
        self._candidate_since: Optional[float] = None

        self._latest_delta: Optional[float] = None
        self._prev_xyz: Optional[Tuple[float, float, float]] = None

        self._stop_event = threading.Event()
        self._cooldown_event = threading.Event()
        self._state_lock = threading.Lock()

        self._sensor_thread: Optional[threading.Thread] = None
        self._temp_thread: Optional[threading.Thread] = None
        self._alerted_thresholds: set[int] = set()  # 送信済みバッテリー閾値
        self._last_battery_check: float = 0.0       # 最後にバッテリーチェックした時刻
        self._candidate_max_delta: float = 0.0      # 離席候補期間中の最大delta

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def start(self) -> None:
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
        logger.info("SensorMonitor 停止要求を受信")
        self._stop_event.set()

    def get_current_state(self) -> dict:
        with self._state_lock:
            return {
                "confirmed_state": self._confirmed_state,
                "candidate_state": self._candidate_state,
                "candidate_since": self._candidate_since,
                "latest_z": None,
                "latest_metric": self._latest_delta,
                "latest_stddev": self._latest_delta,
                "is_cooldown": self._cooldown_event.is_set(),
            }

    # ------------------------------------------------------------------
    # 温度監視ループ
    # ------------------------------------------------------------------

    def _temp_loop(self) -> None:
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

                    self._cooldown_event.set()

                    waited = 0.0
                    step = 1.0
                    while waited < cfg.cooldown_sleep_sec and not self._stop_event.is_set():
                        time.sleep(step)
                        waited += step

                    self._cooldown_event.clear()
                    notifier.notify_resume(cfg.webhook_url, hardware.get_battery_temperature())
                    logger.info("休止終了。センサー監視を再開します。")
                else:
                    if temp is not None:
                        logger.debug(f"バッテリー温度: {temp:.1f}℃")
            except Exception as e:
                logger.exception(f"温度監視ループで例外: {e}")

            self._stop_event.wait(cfg.temp_check_interval_sec)

    # ------------------------------------------------------------------
    # センサーポーリングループ
    # ------------------------------------------------------------------

    def _sensor_loop(self) -> None:
        logger.info(
            f"センサーループ開始（{self.config.sensor_poll_interval_sec}秒ごとにポーリング）"
        )
        while not self._stop_event.is_set():
            if self._cooldown_event.is_set():
                self._stop_event.wait(1.0)
                continue

            try:
                self._poll_once()
            except Exception as e:
                logger.exception(f"ポーリングで例外、次回まで待機: {e}")

            self._stop_event.wait(self.config.sensor_poll_interval_sec)

        logger.info("センサーループ終了")

    def _poll_once(self) -> None:
        """termux-sensor -n 1 で 1 点取得して差分を計算する。"""
        xyz = self._get_single_reading()
        if xyz is None:
            logger.warning("センサー取得失敗、スキップ")
            return

        x, y, z = xyz
        logger.info(f"[取得] X={x:.3f} Y={y:.3f} Z={z:.3f}")

        with self._state_lock:
            prev = self._prev_xyz
            self._prev_xyz = xyz

        if prev is None:
            logger.info("初回取得。次回ポーリングから差分計算を開始します。")
            return

        delta = abs(x - prev[0]) + abs(y - prev[1]) + abs(z - prev[2])

        with self._state_lock:
            self._latest_delta = delta

        logger.info(
            f"[差分] |ΔX|={abs(x-prev[0]):.3f} |ΔY|={abs(y-prev[1]):.3f} "
            f"|ΔZ|={abs(z-prev[2]):.3f}  合計={delta:.3f}  閾値={self.config.variance_threshold}"
        )

        self._evaluate_delta(delta)

        # 30分ごとの定期バッテリーチェック
        now = time.monotonic()
        if now - self._last_battery_check >= 1800:
            self._last_battery_check = now
            self._check_battery_alert()

    def _check_battery_alert(self, level: Optional[int] = None) -> None:
        """バッテリー残量が閾値を下回ったらサマリーchに通知（1閾値1回のみ）。"""
        cfg = self.config
        if not cfg.summary_webhook_url:
            return
        if level is None:
            level = hardware.get_battery_level()
        if level is None:
            return
        for threshold in _BATTERY_ALERT_THRESHOLDS:
            if level <= threshold and threshold not in self._alerted_thresholds:
                self._alerted_thresholds.add(threshold)
                msg = f"⚠️ バッテリー残量が {threshold}% 以下になりました（現在 {level}%）"
                notifier.send_webhook(cfg.summary_webhook_url, msg, username="ChairLogger Alert")
                logger.warning(f"バッテリーアラート送信: {level}% (閾値{threshold}%)")

    def _get_single_reading(self) -> Optional[Tuple[float, float, float]]:
        """termux-sensor -n 1 で XYZ を 1 点取得して返す。失敗時は None。"""
        try:
            r = subprocess.run(
                ["termux-sensor", "-s", "gravity", "-n", "1"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except FileNotFoundError:
            logger.error("termux-sensor が見つかりません。Termux:API をインストールしてください。")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("termux-sensor -n 1 がタイムアウト")
            return None
        except Exception as e:
            logger.exception(f"termux-sensor 実行失敗: {e}")
            return None

        if r.stderr.strip():
            logger.debug(f"[stderr] {r.stderr.strip()[:200]}")

        return self._parse_xyz(r.stdout)

    def _parse_xyz(self, stdout: str) -> Optional[Tuple[float, float, float]]:
        """stdout から XYZ を抽出して返す。"""
        buf: list[str] = []
        depth = 0
        for line in stdout.splitlines():
            buf.append(line)
            depth += line.count("{") - line.count("}")
            if depth <= 0 and buf:
                block = "\n".join(buf).strip()
                buf.clear()
                depth = 0
                if not block:
                    continue
                try:
                    data = json.loads(block)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict):
                    continue
                for key in data:
                    if "gravity" in key.lower():
                        sensor = data[key]
                        if not isinstance(sensor, dict):
                            break
                        values = sensor.get("values")
                        if isinstance(values, list) and len(values) >= 3:
                            try:
                                return (float(values[0]), float(values[1]), float(values[2]))
                            except (TypeError, ValueError):
                                pass
                        break
        logger.debug(f"XYZ 抽出失敗。stdout先頭: {stdout[:100]!r}")
        return None

    # ------------------------------------------------------------------
    # 状態機械（Debounce）
    # ------------------------------------------------------------------

    def _evaluate_delta(self, delta: float) -> None:
        """差分値で着席/離席を判定して Debounce を回す。

        着席: delta >= threshold で即確定（1回で十分）
        離席: delta < threshold が debounce_left_sec 継続で確定
        """
        cfg = self.config
        now = time.monotonic()

        with self._state_lock:
            observed = STATUS_SEATED if delta >= cfg.variance_threshold else STATUS_LEFT

            logger.info(
                f"[判定] delta={delta:.3f} 閾値={cfg.variance_threshold} "
                f"observed={observed} confirmed={self._confirmed_state}"
            )

            if observed == STATUS_SEATED:
                # 着席は1回で即確定。確定済みの場合は離席候補をリセットするだけ。
                if self._confirmed_state != STATUS_SEATED:
                    self._commit_state(STATUS_SEATED, delta)
                self._candidate_state = None
                self._candidate_since = None
                self._candidate_max_delta = 0.0
                return

            # 以下は observed == STATUS_LEFT の処理
            # 確定済みなら何もしない
            if self._confirmed_state == STATUS_LEFT:
                self._candidate_state = None
                self._candidate_since = None
                return

            # 離席候補を開始 or 継続
            if self._candidate_state != STATUS_LEFT:
                self._candidate_state = STATUS_LEFT
                self._candidate_since = now
                self._candidate_max_delta = delta
                return

            # 最大deltaを更新しながら待機
            if delta > self._candidate_max_delta:
                self._candidate_max_delta = delta

            elapsed = now - (self._candidate_since or now)
            logger.info(f"[離席待機] {elapsed:.0f}s / {cfg.debounce_left_sec}s  Δmax: {self._candidate_max_delta:.3f}")
            if elapsed >= cfg.debounce_left_sec:
                self._commit_state(STATUS_LEFT, self._candidate_max_delta)

    def _commit_state(self, new_state: str, delta: float) -> None:
        """確定処理。state_lock 保有中の前提。"""
        cfg = self.config
        prev = self._confirmed_state
        self._confirmed_state = new_state
        self._candidate_state = None
        self._candidate_since = None
        self._candidate_max_delta = 0.0

        logger.info(f"状態確定: {prev} -> {new_state} (delta={delta:.3f})")

        ok = db_models.insert_status(
            cfg.db_path,
            new_state,
            z_value=delta,
            note=None,
        )
        if not ok:
            logger.error("DB書き込みに失敗しました（通知は継続）")

        try:
            battery_level = hardware.get_battery_level()
            if new_state == STATUS_SEATED:
                notifier.notify_seated(cfg.webhook_url, battery_level, delta)
            else:
                notifier.notify_left(cfg.webhook_url, battery_level, delta)
            self._last_battery_check = time.monotonic()
            self._check_battery_alert(battery_level)
        except Exception as e:
            logger.exception(f"通知ハンドラで例外: {e}")
