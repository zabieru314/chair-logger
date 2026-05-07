"""
閾値キャリブレーションスクリプト

使い方:
  python calibrate.py

「座ってください」「離れてください」の指示に従って
実際のZ軸データを収集し、適切な Z_THRESHOLD を自動提案する。
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"
COLLECT_SEC = 10
INTERVAL_MS = 500


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

    # 前回のプロセスを終了してセンサーを解放してから起動
    try:
        subprocess.run(["pkill", "-f", "termux-sensor"], timeout=3)
    except Exception:
        pass
    time.sleep(1.5)  # センサーが解放されるまで待つ

    cmd = ["termux-sensor", "-s", "gravity", "-d", str(INTERVAL_MS)]
    z_values: list[float] = []
    buf: list[str] = []
    depth = 0
    start = time.monotonic()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None

        for line in proc.stdout:
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

        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

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

    # 起動中の main.py とセンサープロセスを停止してから計測する
    print("\n[準備] main.py / termux-sensor を停止しています...", flush=True)
    for target in ["main.py", "termux-sensor"]:
        try:
            subprocess.run(["pkill", "-f", target], timeout=3)
        except Exception:
            pass
    time.sleep(2)
    print("[準備完了] キャリブレーションを開始します。\n", flush=True)

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
        print(f"  参考: 現在の Z_THRESHOLD=5.0 のまま使用することをお勧めします。")
        return

    # 着席Z平均と離席Z平均の中点を閾値として提案
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
