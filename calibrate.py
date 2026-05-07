"""
閾値キャリブレーションスクリプト

使い方:
  python calibrate.py

「座ってください」「離れてください」の指示に従って
実際のZ軸データを収集し、適切な Z_THRESHOLD を自動提案する。

【動作方針】
- main.py が動いていたら SIGSTOP で一時停止（Termux:API接続を保つ）
- termux-sensor の子プロセスだけ SIGKILL で終了
- キャリブレーション完了後に main.py を SIGCONT で再開
"""

from __future__ import annotations

import json
import os
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"
COLLECT_SEC = 10
INTERVAL_MS = 500


def _get_pids(pattern: str) -> list[int]:
    """pattern にマッチするプロセスのPIDリストを返す（自分自身は除く）。"""
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True
        )
        my_pid = os.getpid()
        return [int(p) for p in result.stdout.split() if p.strip().isdigit() and int(p) != my_pid]
    except Exception:
        return []


def _send_signal(pids: list[int], sig: int, label: str) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
            print(f"  {label} → PID={pid}", flush=True)
        except ProcessLookupError:
            pass
        except Exception as e:
            print(f"  {label} PID={pid} 失敗: {e}", flush=True)


def read_sensor_samples(label: str, duration_sec: int, pre_delay_sec: int = 0) -> list[float]:
    """termux-sensor を起動して duration_sec 秒分のZ値を収集する。"""
    print(f"\n{'='*50}")
    print(f"【{label}】")
    input("準備ができたら Enter を押してください...")

    if pre_delay_sec > 0:
        print(f"{pre_delay_sec}秒後に収集開始します。その間に所定の位置についてください。", flush=True)
        for i in range(pre_delay_sec, 0, -1):
            print(f"  {i}秒...", flush=True)
            time.sleep(1)

    print(f"{duration_sec}秒間データを収集します...", flush=True)

    # 残留センサープロセスを終了してから起動
    sensor_pids = _get_pids("termux-sensor")
    if sensor_pids:
        _send_signal(sensor_pids, signal.SIGKILL, "termux-sensor を終了")
        time.sleep(1.5)

    cmd = ["termux-sensor", "-s", "gravity", "-d", str(INTERVAL_MS)]
    z_values: list[float] = []
    buf: list[str] = []
    depth = 0
    start = time.monotonic()

    print(f"  [DBG] Popen起動: {' '.join(cmd)}", flush=True)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        print(f"  [DBG] PID={proc.pid}", flush=True)
        assert proc.stdout is not None

        line_count = 0
        for line in proc.stdout:
            line_count += 1
            if line_count <= 5:
                print(f"  [生出力 L{line_count}] {line.rstrip()}", flush=True)
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
                except json.JSONDecodeError:
                    continue

                sensor = None
                for key in data:
                    if "gravity" in key.lower():
                        sensor = data[key]
                        break

                if not isinstance(sensor, dict):
                    continue

                values = sensor.get("values")
                if isinstance(values, list) and len(values) >= 3:
                    try:
                        z = float(values[2])
                        z_values.append(z)
                        print(f"  サンプル {len(z_values):2d}: Z={z:.3f}", flush=True)
                    except (TypeError, ValueError):
                        continue

        if not z_values:
            print("  [NG] サンプルが1件も取得できませんでした", flush=True)
        else:
            print(f"  [OK] {len(z_values)}件取得完了", flush=True)

        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

        try:
            err = proc.stderr.read() if proc.stderr else ""
            if err.strip():
                print(f"  [stderr] {err.strip()}", flush=True)
        except Exception:
            pass

    except FileNotFoundError:
        print("[ERROR] termux-sensor が見つかりません。Termux:API をインストールしてください。")
        sys.exit(1)

    return z_values


def update_env(threshold: float) -> None:
    """Z_THRESHOLD を .env に書き込む。"""
    if not ENV_FILE.exists():
        print(f"[WARN] {ENV_FILE} が見つかりません。手動で設定してください。")
        return

    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    updated = False
    new_lines = []
    for line in lines:
        if line.startswith("Z_THRESHOLD="):
            new_lines.append(f"Z_THRESHOLD={threshold:.3f}")
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f"Z_THRESHOLD={threshold:.3f}")

    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"✓ .env を更新しました: Z_THRESHOLD={threshold:.3f}")


def main() -> None:
    print("=" * 50)
    print("  着席検知システム 閾値キャリブレーション（Z軸版）")
    print("=" * 50)
    print("スマホをクッションの下（実際の使用位置）に置いた状態で実行してください。")

    # main.py を SIGSTOP で一時停止（Termux:API接続を保つ）
    print("\n[準備] main.py を一時停止します（センサー接続は維持）...", flush=True)
    main_pids = _get_pids("main.py")
    if main_pids:
        _send_signal(main_pids, signal.SIGSTOP, "main.py を一時停止")
        time.sleep(1)
    else:
        print("  main.py は動いていません", flush=True)

    # termux-sensor の子プロセスだけ終了
    sensor_pids = _get_pids("termux-sensor")
    if sensor_pids:
        _send_signal(sensor_pids, signal.SIGKILL, "termux-sensor を終了")
        time.sleep(2)
    else:
        print("  termux-sensor は動いていません", flush=True)

    # センサー応答テスト
    print("\n[確認] センサー応答テスト中...", flush=True)
    try:
        check = subprocess.run(
            ["termux-sensor", "-s", "gravity", "-n", "1"],
            capture_output=True, text=True, timeout=8
        )
        if check.stdout.strip():
            print(f"  [OK] センサー応答あり", flush=True)
        else:
            print(f"  [NG] 応答なし stdout={check.stdout[:100]!r} stderr={check.stderr[:100]!r}", flush=True)
    except subprocess.TimeoutExpired:
        print("  [NG] タイムアウト（センサーが応答しない）", flush=True)
    except Exception as e:
        print(f"  [ERROR] {e}", flush=True)

    print("[準備完了] キャリブレーションを開始します。\n", flush=True)

    try:
        seated_z = read_sensor_samples(
            "着席中: スマホをクッション下に置いて座ってください",
            COLLECT_SEC,
            pre_delay_sec=3,
        )
        left_z = read_sensor_samples(
            "離席中: Enter後5秒で収集開始します。その間にその場を離れてください",
            COLLECT_SEC,
            pre_delay_sec=5,
        )
    finally:
        # 終了時に main.py を必ず再開
        if main_pids:
            print("\n[後処理] main.py を再開します...", flush=True)
            _send_signal(main_pids, signal.SIGCONT, "main.py を再開")

    if not seated_z or not left_z:
        print("[ERROR] データが取得できませんでした。")
        sys.exit(1)

    seated_mean = statistics.mean(seated_z)
    left_mean = statistics.mean(left_z)

    print("\n" + "="*50)
    print("【結果】")
    print(f"  着席中のZ平均: {seated_mean:.3f}  (サンプル数: {len(seated_z)})")
    print(f"  離席中のZ平均: {left_mean:.3f}  (サンプル数: {len(left_z)})")

    if seated_mean <= left_mean:
        print("\n[WARNING] 着席中のZ値が離席中以下です。")
        print("  → スマホの置き方を確認して、もう一度試してください。")
        print("  参考: 現在の Z_THRESHOLD=5.0 のまま使用することをお勧めします。")
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
