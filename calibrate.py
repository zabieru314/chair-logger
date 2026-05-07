"""
閾値キャリブレーションスクリプト（デバッグ強化版）

使い方:
  1. main.py を Ctrl+C で停止してから
  2. python calibrate.py を同じセッションで実行
"""

from __future__ import annotations

import json
import os
import select
import signal
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"
COLLECT_SEC = 10
INTERVAL_MS = 500


# ---------------------------------------------------------------
# デバッグユーティリティ
# ---------------------------------------------------------------

def _log(msg: str) -> None:
    print(msg, flush=True)


def _kill_all_sensor_procs() -> None:
    """termux-sensor プロセスを全て SIGTERM → 1秒待ち → SIGKILL で確実に終了。"""
    result = subprocess.run(["pgrep", "-f", "termux-sensor"], capture_output=True, text=True)
    pids = [int(p) for p in result.stdout.split() if p.strip().isdigit()]
    if not pids:
        _log("  [DBG] 残留 termux-sensor なし")
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            _log(f"  [DBG] SIGTERM → PID={pid}")
        except ProcessLookupError:
            pass
    time.sleep(1.0)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
            _log(f"  [DBG] SIGKILL → PID={pid} (残留していた場合)")
        except ProcessLookupError:
            pass  # SIGTERM で既に死んでいれば正常
    time.sleep(1.5)


def _preflight_check() -> None:
    """センサー応答を複数の名前で試す。"""
    _log("\n[事前確認] 各センサー名で -n 1 テスト...")
    for name in ["gravity", "Gravity Sensor", "Gravity"]:
        _log(f"  → name={name!r} ...")
        try:
            r = subprocess.run(
                ["termux-sensor", "-s", name, "-n", "1"],
                capture_output=True, text=True, timeout=5
            )
            _log(f"     returncode={r.returncode}")
            _log(f"     stdout={r.stdout[:200]!r}")
            _log(f"     stderr={r.stderr[:200]!r}")
        except subprocess.TimeoutExpired:
            _log(f"     [TO] タイムアウト（5秒で無応答）")
        except Exception as e:
            _log(f"     [ERR] {e}")


# ---------------------------------------------------------------
# サンプル収集
# ---------------------------------------------------------------

def read_sensor_samples(label: str, duration_sec: int, pre_delay_sec: int = 0) -> list[float]:
    print(f"\n{'='*50}")
    print(f"【{label}】")
    input("準備ができたら Enter を押してください...")

    if pre_delay_sec > 0:
        print(f"{pre_delay_sec}秒後に収集開始します。", flush=True)
        for i in range(pre_delay_sec, 0, -1):
            print(f"  {i}秒...", flush=True)
            time.sleep(1)

    print(f"{duration_sec}秒間データを収集します...", flush=True)

    _kill_all_sensor_procs()

    cmd = ["termux-sensor", "-s", "gravity", "-d", str(INTERVAL_MS)]
    z_values: list[float] = []
    buf: list[str] = []
    depth = 0
    start = time.monotonic()

    _log(f"  [DBG] Popen起動: {' '.join(cmd)}")
    _log(f"  [DBG] 環境: PATH={os.environ.get('PATH','')[:80]}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=os.environ.copy(),
        )
        _log(f"  [DBG] PID={proc.pid}")

        # stderr を別スレッドで読んでリアルタイム表示
        stderr_lines: list[str] = []
        def _read_stderr() -> None:
            assert proc.stderr
            for line in proc.stderr:
                msg = line.rstrip()
                stderr_lines.append(msg)
                _log(f"  [ERR] {msg}")
        threading.Thread(target=_read_stderr, daemon=True).start()

        # 最初の5秒でデータが来るか select で確認
        _log("  [DBG] 最初の5秒でstdoutにデータが来るか確認...")
        assert proc.stdout
        readable, _, _ = select.select([proc.stdout], [], [], 5.0)
        if not readable:
            _log(f"  [NG] 5秒経過してもstdoutにデータなし（プロセス生存: {proc.poll() is None}）")
            if proc.poll() is not None:
                _log(f"  [DBG] プロセス終了コード: {proc.returncode}")
            proc.terminate()
            proc.wait(timeout=3)
            return z_values
        else:
            _log("  [OK] stdoutにデータ到着！読み取り開始")

        # 通常読み取りループ
        line_count = 0
        for line in proc.stdout:
            line_count += 1
            if line_count <= 8:
                _log(f"  [生出力 L{line_count}] {line.rstrip()}")
            if time.monotonic() - start >= duration_sec:
                break

            buf.append(line)
            depth += line.count("{") - line.count("}")
            if depth <= 0 and buf:
                block = "".join(buf).strip()
                buf.clear()
                depth = 0
                if not block:
                    continue
                try:
                    data = json.loads(block)
                except json.JSONDecodeError as e:
                    _log(f"  [DBG] JSONパース失敗: {e} block={block[:60]!r}")
                    continue

                sensor = None
                for key in data:
                    if "gravity" in key.lower():
                        sensor = data[key]
                        break
                if not isinstance(sensor, dict):
                    _log(f"  [DBG] gravityキー見つからず keys={list(data.keys())}")
                    continue

                values = sensor.get("values")
                if isinstance(values, list) and len(values) >= 3:
                    try:
                        z = float(values[2])
                        z_values.append(z)
                        print(f"  サンプル {len(z_values):2d}: Z={z:.3f}", flush=True)
                    except (TypeError, ValueError) as e:
                        _log(f"  [DBG] Z値変換失敗: {e}")
                else:
                    _log(f"  [DBG] values不正: {values!r}")

    except FileNotFoundError:
        _log("[ERROR] termux-sensor が見つかりません。")
        sys.exit(1)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    if not z_values:
        _log("  [NG] サンプル0件")
    else:
        _log(f"  [OK] {len(z_values)}件取得完了")

    return z_values


# ---------------------------------------------------------------
# .env 更新
# ---------------------------------------------------------------

def update_env(threshold: float) -> None:
    if not ENV_FILE.exists():
        _log(f"[WARN] {ENV_FILE} が見つかりません。")
        return
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    new_lines, updated = [], False
    for line in lines:
        if line.startswith("Z_THRESHOLD="):
            new_lines.append(f"Z_THRESHOLD={threshold:.3f}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"Z_THRESHOLD={threshold:.3f}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    _log(f"✓ .env を更新しました: Z_THRESHOLD={threshold:.3f}")


# ---------------------------------------------------------------
# メイン
# ---------------------------------------------------------------

def main() -> None:
    print("=" * 50)
    print("  着席検知システム 閾値キャリブレーション（Z軸版）")
    print("=" * 50)
    print("※ main.py を先に Ctrl+C で停止してから実行してください")
    print(f"  実行PID={os.getpid()} / Python={sys.executable}", flush=True)

    # 残留プロセス確認
    _log("\n[準備] 残留プロセス確認...")
    for pat in ["main.py", "termux-sensor"]:
        r = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True)
        pids = r.stdout.strip()
        _log(f"  {pat}: PID={pids if pids else 'なし'}")

    # termux-sensor だけ終了（main.py には触らない）
    _log("\n[準備] termux-sensor を停止...")
    _kill_all_sensor_procs()

    # 事前センサー確認
    _preflight_check()

    _log("\n[準備完了] キャリブレーションを開始します。\n")

    seated_z = read_sensor_samples(
        "着席中: スマホをクッション下に置いて座ってください",
        COLLECT_SEC, pre_delay_sec=3,
    )
    left_z = read_sensor_samples(
        "離席中: Enter後5秒で収集開始。その間にその場を離れてください",
        COLLECT_SEC, pre_delay_sec=5,
    )

    if not seated_z or not left_z:
        _log("[ERROR] データが取得できませんでした。")
        sys.exit(1)

    seated_mean = statistics.mean(seated_z)
    left_mean = statistics.mean(left_z)

    print("\n" + "="*50)
    print("【結果】")
    print(f"  着席中のZ平均: {seated_mean:.3f}  (サンプル数: {len(seated_z)})")
    print(f"  離席中のZ平均: {left_mean:.3f}  (サンプル数: {len(left_z)})")

    if seated_mean <= left_mean:
        _log("\n[WARNING] 着席中のZ値が離席中以下です。スマホの置き方を確認してください。")
        return

    suggested = (seated_mean + left_mean) / 2.0
    print(f"\n【推奨 Z_THRESHOLD】: {suggested:.3f}")
    print(f"  （着席平均 {seated_mean:.3f} と 離席平均 {left_mean:.3f} の中点）")

    answer = input("\n.env を自動更新しますか？ [y/N]: ").strip().lower()
    if answer == "y":
        update_env(round(suggested, 3))
        print("\n次回 python main.py を実行すると新しい閾値が適用されます。")
    else:
        print(f"\n手動で .env の Z_THRESHOLD={suggested:.3f} に変更してください。")


if __name__ == "__main__":
    main()
