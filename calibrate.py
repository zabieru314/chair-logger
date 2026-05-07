"""
閾値キャリブレーションスクリプト

使い方:
  python calibrate.py

「座ってください」「離れてください」の指示に従って
実際の振動データを収集し、適切な VIBRATION_THRESHOLD を自動提案する。
"""

from __future__ import annotations

import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"
COLLECT_SEC = 10
INTERVAL_MS = 500


def read_sensor_samples(label: str, duration_sec: int) -> list[float]:
    """termux-sensor を起動して duration_sec 秒分の magnitude を収集する。"""
    print(f"\n{'='*50}")
    print(f"【{label}】の状態でじっとしてください。")
    input("準備ができたら Enter を押してください...")
    print(f"{duration_sec}秒間データを収集します...", flush=True)

    cmd = ["termux-sensor", "-s", "accelerometer", "-d", str(INTERVAL_MS)]
    magnitudes: list[float] = []
    buf: list[str] = []
    depth = 0
    start = time.monotonic()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        assert proc.stdout is not None

        for raw in proc.stdout:
            if time.monotonic() - start >= duration_sec:
                break

            line = raw.decode("utf-8", errors="replace")
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
                    if "accelerometer" in key.lower():
                        sensor = data[key]
                        break

                if not isinstance(sensor, dict):
                    continue

                values = sensor.get("values")
                if isinstance(values, list) and len(values) >= 3:
                    try:
                        x, y, z = float(values[0]), float(values[1]), float(values[2])
                        mag = math.sqrt(x*x + y*y + z*z)
                        magnitudes.append(mag)
                        print(f"  サンプル {len(magnitudes):2d}: magnitude={mag:.3f}", flush=True)
                    except (TypeError, ValueError):
                        continue

        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    except FileNotFoundError:
        print("[ERROR] termux-sensor が見つかりません。Termux:API をインストールしてください。")
        sys.exit(1)

    return magnitudes


def calc_stddev(magnitudes: list[float]) -> float:
    if len(magnitudes) < 2:
        return 0.0
    return statistics.pstdev(magnitudes)


def update_env(threshold: float) -> None:
    """VIBRATION_THRESHOLD を .env に書き込む。"""
    if not ENV_FILE.exists():
        print(f"[WARN] {ENV_FILE} が見つかりません。手動で設定してください。")
        return

    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    updated = False
    new_lines = []
    for line in lines:
        if line.startswith("VIBRATION_THRESHOLD="):
            new_lines.append(f"VIBRATION_THRESHOLD={threshold:.3f}")
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f"VIBRATION_THRESHOLD={threshold:.3f}")

    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"✓ .env を更新しました: VIBRATION_THRESHOLD={threshold:.3f}")


def main() -> None:
    print("=" * 50)
    print("  着席検知システム 閾値キャリブレーション")
    print("=" * 50)
    print("スマホをクッションの下（実際の使用位置）に置いた状態で実行してください。")

    seated_mags = read_sensor_samples("着席中（座ってください）", COLLECT_SEC)
    left_mags = read_sensor_samples("離席中（席を離れてください）", COLLECT_SEC)

    if not seated_mags or not left_mags:
        print("[ERROR] データが取得できませんでした。")
        sys.exit(1)

    seated_stddev = calc_stddev(seated_mags)
    left_stddev = calc_stddev(left_mags)

    print("\n" + "="*50)
    print("【結果】")
    print(f"  着席中の振動(stddev): {seated_stddev:.4f}  (サンプル数: {len(seated_mags)})")
    print(f"  離席中の振動(stddev): {left_stddev:.4f}  (サンプル数: {len(left_mags)})")

    if seated_stddev <= left_stddev:
        print("\n[WARNING] 着席中の振動が離席中以下です。")
        print("  → スマホの置き場所を変えるか、もう一度試してください。")
        print(f"  参考: 現在の VIBRATION_THRESHOLD=0.3 のまま使用することをお勧めします。")
        return

    # 中間値を閾値として提案（着席stddevと離席stddevの中点）
    suggested = (seated_stddev + left_stddev) / 2
    margin = (seated_stddev - left_stddev) * 0.3
    suggested = left_stddev + margin + (seated_stddev - left_stddev) * 0.2

    print(f"\n【推奨 VIBRATION_THRESHOLD】: {suggested:.3f}")
    print(f"  （着席{seated_stddev:.3f} と 離席{left_stddev:.3f} の間に設定）")

    answer = input("\n.env を自動更新しますか？ [y/N]: ").strip().lower()
    if answer == "y":
        update_env(round(suggested, 3))
        print("\n次回 python main.py を実行すると新しい閾値が適用されます。")
    else:
        print(f"\n手動で .env の VIBRATION_THRESHOLD={suggested:.3f} に変更してください。")


if __name__ == "__main__":
    main()
