"""
閾値キャリブレーションスクリプト（1セッション版）

【設計方針】
  termux-sensor を1回だけ起動し、同一の Popen ストリームから
  着席・離席の両サンプルを連続収集する。
  途中で proc.terminate() を呼ばないことで Termux:API 接続を保護する。
"""

from __future__ import annotations

import json
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


def _log(msg: str) -> None:
    print(msg, flush=True)



def _countdown(seconds: int) -> None:
    for i in range(seconds, 0, -1):
        _log(f"  {i}秒...")
        time.sleep(1)


def _collect_window(
    all_samples: list[tuple[float, float]],
    label: str,
    pre_delay_sec: int,
) -> tuple[float, float]:
    """ユーザーにカウントダウンを見せ、収集ウィンドウの開始・終了時刻を返す。"""
    print(f"\n{'='*50}")
    print(f"【{label}】")
    input("準備ができたら Enter を押してください... ")

    if pre_delay_sec > 0:
        _log(f"{pre_delay_sec}秒後に収集開始します。")
        _countdown(pre_delay_sec)

    _log(f"{COLLECT_SEC}秒間データを収集します...")
    start = time.monotonic()
    snapshot = len(all_samples)

    for sec in range(1, COLLECT_SEC + 1):
        time.sleep(1)
        total_new = len(all_samples) - snapshot
        latest_z = all_samples[-1][1] if all_samples else None
        if latest_z is not None:
            _log(f"  {sec:2d}秒経過 / 収集{total_new}件 / 最新Z={latest_z:.3f}")
        else:
            _log(f"  {sec:2d}秒経過 / 収集{total_new}件 / データ待ち...")

    end = time.monotonic()
    return start, end


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
    print("  ※ termux-sensor は1回起動して着席・離席を連続収集します")
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
        _log("   必ず Ctrl+C か Ctrl+\\ で止めてから再実行してください。")
        sys.exit(1)

    _log("  競合プロセスなし → 続行")

    # ---------------------------------------------------------------
    # termux-sensor を1回だけ起動（-n で自然終了させることで binder を保護）
    # ---------------------------------------------------------------
    # 120サンプル = 60秒。収集完了後に自然終了を待つことで binder が綺麗に解放される。
    # terminate/kill で止めると binder が壊れて次回実行が失敗するため絶対に使わない。
    N_SAMPLES = 120
    cmd = ["termux-sensor", "-s", "gravity", "-d", str(INTERVAL_MS), "-n", str(N_SAMPLES)]
    _log(f"\n[起動] {' '.join(cmd)} （{N_SAMPLES * INTERVAL_MS // 1000}秒分）")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        _log("[ERROR] termux-sensor が見つかりません。")
        sys.exit(1)

    _log(f"  PID={proc.pid}")

    # バックグラウンドスレッドで全サンプルを (time, z) のリストに蓄積
    all_samples: list[tuple[float, float]] = []

    def _reader() -> None:
        assert proc.stdout
        buf: list[str] = []
        depth = 0
        for line in proc.stdout:
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
                for key in data:
                    if "gravity" in key.lower():
                        sensor = data[key]
                        if isinstance(sensor, dict):
                            values = sensor.get("values")
                            if isinstance(values, list) and len(values) >= 3:
                                try:
                                    z = float(values[2])
                                    all_samples.append((time.monotonic(), z))
                                except (TypeError, ValueError):
                                    pass
                        break

    threading.Thread(target=_reader, daemon=True).start()

    def _stderr_reader() -> None:
        assert proc.stderr
        for line in proc.stderr:
            _log(f"  [STDERR] {line.rstrip()}")

    threading.Thread(target=_stderr_reader, daemon=True).start()

    # 最初のデータが来るまで最大5秒待機
    _log("  データ到着確認中（最大5秒）...")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if all_samples:
            _log(f"  [OK] データ到着！ 最初のZ={all_samples[0][1]:.3f}")
            break
        time.sleep(0.1)
    else:
        _log("  [NG] 5秒待ってもデータが来ません。Termux:API を確認してください。")
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        sys.exit(1)

    # ---------------------------------------------------------------
    # フェーズ1: 着席サンプル収集
    # ---------------------------------------------------------------
    seated_start, seated_end = _collect_window(
        all_samples,
        "着席中: スマホをクッション下に置いて座ってください",
        pre_delay_sec=3,
    )

    # ---------------------------------------------------------------
    # フェーズ2: 離席サンプル収集（同じ Popen から継続）
    # ---------------------------------------------------------------
    left_start, left_end = _collect_window(
        all_samples,
        "離席中: Enter後5秒で収集開始。その間にその場を離れてください",
        pre_delay_sec=5,
    )

    # ---------------------------------------------------------------
    # センサーが自然終了するまで待つ（binder を綺麗に解放するために必須）
    # terminate/kill を使わず、-n で指定したサンプル数を撃ち切って自然終了を待つ。
    # ---------------------------------------------------------------
    _log("\nセンサー自然終了を待っています（最大60秒）...")
    try:
        proc.wait(timeout=60)
        _log("  [OK] センサー正常終了。次回もすぐ使えます。")
    except subprocess.TimeoutExpired:
        _log("  [WARN] タイムアウト - 強制終了します（次回は再起動が必要な場合あり）")
        proc.kill()
        proc.wait()

    # ---------------------------------------------------------------
    # 時間窓でサンプルを切り出して集計
    # ---------------------------------------------------------------
    seated_z = [z for t, z in all_samples if seated_start <= t <= seated_end]
    left_z = [z for t, z in all_samples if left_start <= t <= left_end]

    _log(f"\n[集計] 着席: {len(seated_z)}件 / 離席: {len(left_z)}件")

    if not seated_z or not left_z:
        _log("[ERROR] サンプルが不足しています。")
        if not seated_z:
            _log("  → 着席サンプルが0件です")
        if not left_z:
            _log("  → 離席サンプルが0件です")
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
