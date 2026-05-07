"""
閾値キャリブレーションスクリプト

【設計方針】
  フェーズごとに subprocess.run + "-n 20" で独立した短期セッションを使う。
  長いセッション(-n 120 等)は自然終了後も Termux:API binder を壊すため使わない。
  "-n 1" を含む短期セッションは何度でも安全に呼べることを確認済み。
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
N_SAMPLES = 20      # 1フェーズあたりのサンプル数（500ms×20 = 10秒）
INTERVAL_MS = 500


def _log(msg: str) -> None:
    print(msg, flush=True)


def _countdown(seconds: int) -> None:
    for i in range(seconds, 0, -1):
        _log(f"  {i}秒...")
        time.sleep(1)


def _parse_z_values(stdout: str) -> list[float]:
    """termux-sensor の stdout から Z 値リストを取り出す。"""
    z_values: list[float] = []
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
            for key in data:
                if "gravity" in key.lower():
                    sensor = data[key]
                    if isinstance(sensor, dict):
                        values = sensor.get("values")
                        if isinstance(values, list) and len(values) >= 3:
                            try:
                                z_values.append(float(values[2]))
                            except (TypeError, ValueError):
                                pass
                    break
    return z_values


def collect_phase(label: str, pre_delay_sec: int) -> list[float]:
    """1フェーズ分のサンプルを収集して返す。"""
    print(f"\n{'='*50}")
    print(f"【{label}】")
    input("準備ができたら Enter を押してください... ")

    if pre_delay_sec > 0:
        _log(f"{pre_delay_sec}秒後に収集開始します。")
        _countdown(pre_delay_sec)

    _log(f"収集中...（約{N_SAMPLES * INTERVAL_MS // 1000}秒）")

    try:
        r = subprocess.run(
            ["termux-sensor", "-s", "gravity", "-d", str(INTERVAL_MS), "-n", str(N_SAMPLES)],
            capture_output=True,
            text=True,
            timeout=N_SAMPLES * INTERVAL_MS / 1000 + 10,
        )
    except subprocess.TimeoutExpired:
        _log("  [NG] タイムアウト - Termux:API を確認してください。")
        return []
    except FileNotFoundError:
        _log("[ERROR] termux-sensor が見つかりません。")
        sys.exit(1)

    if r.stderr.strip():
        _log(f"  [STDERR] {r.stderr.strip()[:200]}")

    z_values = _parse_z_values(r.stdout)

    if not z_values:
        _log("  [NG] サンプルが取れませんでした。")
    else:
        for i, z in enumerate(z_values, 1):
            _log(f"  サンプル {i:2d}/{N_SAMPLES}: Z={z:.3f}")
        _log(f"  [OK] {len(z_values)}件取得完了")

    return z_values


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


def main() -> None:
    print("=" * 50)
    print("  着席検知システム 閾値キャリブレーション")
    print(f"  各フェーズ: -n {N_SAMPLES} ({N_SAMPLES * INTERVAL_MS // 1000}秒)の独立セッション")
    print("=" * 50)

    # 競合プロセスが存在する場合は EXIT（kill はしない）
    _log("\n[準備] 競合プロセスを確認...")
    conflicts: list[str] = []
    for pat in ["main.py", "termux-sensor"]:
        r = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True)
        pids = r.stdout.strip()
        if pids:
            conflicts.append(f"  {pat}: PID={pids}")

    if conflicts:
        _log("[ERROR] 以下のプロセスが動いています。先に Ctrl+C で停止してください：")
        for c in conflicts:
            _log(c)
        _log("\n⚠️  pkill -9 や kill -9 は使わないこと（Termux:API が壊れる）。")
        sys.exit(1)

    _log("  競合プロセスなし → 続行\n")

    # フェーズ1: 着席
    seated_z = collect_phase(
        "着席中: スマホをクッション下に置いて座ってください",
        pre_delay_sec=3,
    )

    # フェーズ2: 離席（前フェーズの短期セッションが終了してから新しいセッション開始）
    left_z = collect_phase(
        "離席中: Enter後5秒で収集開始。その間にその場を離れてください",
        pre_delay_sec=5,
    )

    if not seated_z or not left_z:
        _log("[ERROR] データが取得できませんでした。")
        sys.exit(1)

    seated_mean = statistics.mean(seated_z)
    left_mean = statistics.mean(left_z)

    print("\n" + "=" * 50)
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
