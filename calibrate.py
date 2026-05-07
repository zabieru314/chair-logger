"""
閾値キャリブレーションスクリプト（差分メトリクス版）

【設計方針】
  フェーズごとに subprocess.run + "-n 20" で独立した短期セッションを使う。
  各フェーズ終了後に termux-sensor -c でリスナーを明示的に解放する。
  着席/離席それぞれの mean(|ΔX|+|ΔY|) を計測し、中点を VARIANCE_THRESHOLD として保存する。
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


def _sensor_cleanup() -> None:
    """Termux:API のセンサーリスナーを明示的に解放する。"""
    try:
        subprocess.run(
            ["termux-sensor", "-c"],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def _parse_xy_values(stdout: str) -> list[tuple[float, float]]:
    """termux-sensor の stdout から (X, Y) 値リストを取り出す。"""
    xy_values: list[tuple[float, float]] = []
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
                                xy_values.append((float(values[0]), float(values[1])))
                            except (TypeError, ValueError):
                                pass
                    break
    return xy_values


def _calc_diff_metric(xy: list[tuple[float, float]]) -> float:
    """連続サンプル間の平均変化量 mean(|ΔX|+|ΔY|) を返す。"""
    if len(xy) < 2:
        return 0.0
    total = sum(
        abs(xy[i][0] - xy[i - 1][0]) + abs(xy[i][1] - xy[i - 1][1])
        for i in range(1, len(xy))
    )
    return total / (len(xy) - 1)


def collect_phase(label: str, pre_delay_sec: int) -> list[tuple[float, float]]:
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
        _log("  [NG] タイムアウト - termux-sensor -c でリセット後に再試行してください。")
        _sensor_cleanup()
        return []
    except FileNotFoundError:
        _log("[ERROR] termux-sensor が見つかりません。")
        sys.exit(1)
    finally:
        _sensor_cleanup()

    if r.stderr.strip():
        _log(f"  [STDERR] {r.stderr.strip()[:200]}")

    xy_values = _parse_xy_values(r.stdout)

    if not xy_values:
        _log("  [NG] サンプルが取れませんでした。")
    else:
        for i, (x, y) in enumerate(xy_values, 1):
            _log(f"  サンプル {i:2d}/{N_SAMPLES}: X={x:.3f} Y={y:.3f}")
        metric = _calc_diff_metric(xy_values)
        _log(f"  [OK] {len(xy_values)}件取得完了  メトリクス={metric:.4f}")

    return xy_values


def update_env(threshold: float) -> None:
    if not ENV_FILE.exists():
        _log(f"[WARN] {ENV_FILE} が見つかりません。")
        return
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    new_lines, updated = [], False
    for line in lines:
        if line.startswith("VARIANCE_THRESHOLD="):
            new_lines.append(f"VARIANCE_THRESHOLD={threshold:.4f}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"VARIANCE_THRESHOLD={threshold:.4f}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    _log(f"✓ .env を更新しました: VARIANCE_THRESHOLD={threshold:.4f}")


def main() -> None:
    print("=" * 50)
    print("  着席検知システム 閾値キャリブレーション（差分メトリクス版）")
    print(f"  各フェーズ: -n {N_SAMPLES} ({N_SAMPLES * INTERVAL_MS // 1000}秒) + -c クリーンアップ")
    print("  判定方式: mean(|ΔX|+|ΔY|) 連続サンプル差分")
    print("=" * 50)

    _log("\n[準備] main.py が動いていないか確認...")
    r = subprocess.run(["pgrep", "-f", "main.py"], capture_output=True, text=True)
    if r.stdout.strip():
        _log(f"[ERROR] main.py が動いています（PID={r.stdout.strip()}）。")
        _log("  Ctrl+C で停止してから再実行してください。")
        sys.exit(1)

    _log("[準備] termux-sensor -c でリスナーをクリーンアップ...")
    _sensor_cleanup()
    _log("  完了 → 続行\n")

    # フェーズ1: 着席
    seated_xy = collect_phase(
        "着席中: スマホをクッション下に置いて座ってください",
        pre_delay_sec=3,
    )

    # フェーズ2: 離席
    left_xy = collect_phase(
        "離席中: Enter後5秒で収集開始。その間にその場を離れてください",
        pre_delay_sec=5,
    )

    if not seated_xy or not left_xy:
        _log("[ERROR] データが取得できませんでした。")
        sys.exit(1)

    seated_metric = _calc_diff_metric(seated_xy)
    left_metric = _calc_diff_metric(left_xy)

    print("\n" + "=" * 50)
    print("【結果】")
    print(f"  着席中のメトリクス: {seated_metric:.4f}  (サンプル数: {len(seated_xy)})")
    print(f"  離席中のメトリクス: {left_metric:.4f}  (サンプル数: {len(left_xy)})")

    if seated_metric <= left_metric:
        _log("\n[WARNING] 着席中のメトリクスが離席中以下です。")
        _log("  スマホをクッション下にしっかり置いているか確認し、再実行してください。")
        return

    suggested = (seated_metric + left_metric) / 2.0
    print(f"\n【推奨 VARIANCE_THRESHOLD】: {suggested:.4f}")
    print(f"  （着席={seated_metric:.4f} と 離席={left_metric:.4f} の中点）")

    answer = input("\n.env を自動更新しますか？ [y/N]: ").strip().lower()
    if answer == "y":
        update_env(round(suggested, 4))
        print("\n次回 python main.py を実行すると新しい閾値が適用されます。")
    else:
        print(f"\n手動で .env の VARIANCE_THRESHOLD={suggested:.4f} に変更してください。")


if __name__ == "__main__":
    main()
