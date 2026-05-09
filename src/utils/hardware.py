"""
ハードウェア監視ユーティリティ

termux-battery-status を呼び出してバッテリー温度を取得し、
閾値を超えていないか判定する。

設計方針:
- termux-battery-status はワンショット呼び出し（数秒で完了）なので Popen ではなく
  subprocess.run + タイムアウトで安全に呼ぶ。
- 取得失敗時は None を返し、呼び出し側で「取得失敗」として扱う（落とさない）。
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


def is_termux_available() -> bool:
    """termux-battery-status コマンドが利用可能か確認する。"""
    return shutil.which("termux-battery-status") is not None


def get_battery_status(timeout_sec: float = 5.0) -> Optional[dict]:
    """
    termux-battery-status を呼び出してバッテリー情報を辞書で返す。

    取得失敗時は None を返す。
    """
    if not is_termux_available():
        logger.warning("termux-battery-status が見つかりません。Termux環境でのみ動作します。")
        return None

    try:
        result = subprocess.run(
            ["termux-battery-status"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.error("termux-battery-status がタイムアウトしました。")
        return None
    except Exception as e:
        logger.exception(f"termux-battery-status の実行で例外: {e}")
        return None

    if result.returncode != 0:
        logger.error(
            f"termux-battery-status が異常終了 (code={result.returncode}): {result.stderr.strip()}"
        )
        return None

    try:
        data = json.loads(result.stdout)
        return data
    except json.JSONDecodeError as e:
        logger.error(f"termux-battery-status の出力がJSONとして解釈できませんでした: {e}")
        return None


def get_battery_temperature() -> Optional[float]:
    """
    バッテリー温度（摂氏）を取得する。

    取得失敗時は None。
    """
    status = get_battery_status()
    if status is None:
        return None

    temp = status.get("temperature")
    if temp is None:
        logger.warning("termux-battery-status に temperature キーがありません。")
        return None

    try:
        return float(temp)
    except (TypeError, ValueError):
        logger.error(f"温度値の変換に失敗: {temp!r}")
        return None


def get_battery_level() -> Optional[int]:
    """バッテリー残量（0〜100）を取得する。取得失敗時は None。

    /sys/class/power_supply から直読み（Termux:API 不要・高速）を優先し、
    失敗した場合のみ termux-battery-status にフォールバックする。
    """
    # /sys から直読み（権限不要・Termux:API の binder 状態に依存しない）
    for path in (
        "/sys/class/power_supply/battery/capacity",
        "/sys/class/power_supply/mtk-battery/capacity",
    ):
        try:
            with open(path) as f:
                return int(f.read().strip())
        except Exception:
            pass

    # フォールバック: termux-battery-status
    status = get_battery_status()
    if status is None:
        return None
    pct = status.get("percentage")
    if pct is None:
        logger.warning("termux-battery-status に percentage キーがありません。")
        return None
    try:
        return int(pct)
    except (TypeError, ValueError):
        logger.error(f"残量値の変換に失敗: {pct!r}")
        return None


def is_overheating(max_temp_celsius: float) -> tuple[bool, Optional[float]]:
    """
    現在のバッテリー温度が閾値を超えているかを判定する。

    Returns:
        (超過フラグ, 現在温度)。温度取得失敗時は (False, None)。
    """
    temp = get_battery_temperature()
    if temp is None:
        return False, None
    return temp >= max_temp_celsius, temp
