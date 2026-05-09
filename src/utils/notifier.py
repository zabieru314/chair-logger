"""
Webhook通知ユーティリティ

Discord/Slack/Teams等のWebhookエンドポイントへJSONをPOSTする。
ネットワーク失敗で本体プロセスが落ちないよう、すべて try-except で握りつぶす。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# デフォルトのリクエストタイムアウト（秒）
DEFAULT_TIMEOUT = 10.0


def _progress_str(seated_min: Optional[int], goal_min: int) -> str:
    """目標進捗文字列を生成する。goal_min=0 または seated_min=None なら空文字。"""
    if goal_min <= 0 or seated_min is None:
        return ""
    h_s, m_s = divmod(seated_min, 60)
    h_g, m_g = divmod(goal_min, 60)
    seated_str = f"{h_s}時間{m_s}分" if h_s else f"{m_s}分"
    goal_str = f"{h_g}時間{m_g}分" if h_g else f"{m_g}分"
    pct = min(int(seated_min * 100 / goal_min), 100)
    return f"  ⏱ {seated_str}/{goal_str}（{pct}%）"


def send_webhook(webhook_url: Optional[str], content: str, username: str = "ChairLogger") -> bool:
    """
    汎用Webhook送信。

    Discord互換のJSON（`content` / `username`）でPOSTする。
    Slack の Incoming Webhook も `text` フィールドで動くため
    両者を含めた payload を投げる。

    Returns:
        送信に成功したら True、失敗したら False。
    """
    if not webhook_url:
        logger.debug("WEBHOOK_URL 未設定のため通知をスキップします。")
        return False

    payload = {
        "content": content,
        "username": username,
        "text": content,  # Slack互換
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=DEFAULT_TIMEOUT)
        if 200 <= resp.status_code < 300:
            logger.info(f"Webhook通知成功: {content[:80]}")
            return True
        logger.error(
            f"Webhook通知失敗: status={resp.status_code} body={resp.text[:200]}"
        )
        return False
    except requests.RequestException as e:
        logger.error(f"Webhook通知でネットワーク例外: {e}")
        return False
    except Exception as e:
        logger.exception(f"Webhook通知で予期しない例外: {e}")
        return False


def notify_seated(webhook_url: Optional[str], battery_level: Optional[int] = None, max_delta: Optional[float] = None, seated_min: Optional[int] = None, goal_min: int = 0) -> bool:
    """着席確定時の通知。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    batt = f"  🔋{battery_level}%" if battery_level is not None else ""
    delta_str = f"  Δ: {max_delta:.3f}" if max_delta is not None else ""
    progress = _progress_str(seated_min, goal_min)
    return send_webhook(webhook_url, f"[着席] {now} 作業を開始しました。{batt}{delta_str}{progress}")


def notify_left(webhook_url: Optional[str], battery_level: Optional[int] = None, max_delta: Optional[float] = None, seated_min: Optional[int] = None, goal_min: int = 0) -> bool:
    """離席確定時の通知。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    batt = f"  🔋{battery_level}%" if battery_level is not None else ""
    delta_str = f"  Δmax: {max_delta:.3f}" if max_delta is not None else ""
    progress = _progress_str(seated_min, goal_min)
    return send_webhook(webhook_url, f"[離席] {now} 席を離れました。{batt}{delta_str}{progress}")


def notify_overheat(webhook_url: Optional[str], temperature: float, cooldown_sec: int) -> bool:
    """温度超過時の警告通知。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = (
        f"[警告] {now} バッテリー温度が {temperature:.1f}℃ に達しました。"
        f"安全のためセンサー監視を {cooldown_sec} 秒休止します。"
    )
    return send_webhook(webhook_url, msg)


def notify_resume(webhook_url: Optional[str], temperature: Optional[float]) -> bool:
    """休止からの復帰通知。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if temperature is not None:
        msg = f"[復帰] {now} 温度 {temperature:.1f}℃ に低下したためセンサー監視を再開します。"
    else:
        msg = f"[復帰] {now} 休止時間が経過したためセンサー監視を再開します。"
    return send_webhook(webhook_url, msg)


def notify_startup(webhook_url: Optional[str], battery_level: Optional[int] = None, seated_min: Optional[int] = None, goal_min: int = 0) -> bool:
    """システム起動通知。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    batt = f"  🔋{battery_level}%" if battery_level is not None else ""
    progress = _progress_str(seated_min, goal_min)
    return send_webhook(webhook_url, f"[起動] {now} 着席検知システムを起動しました。{batt}{progress}")


def notify_summary(
    webhook_url: Optional[str],
    periods: list[tuple[str, str, int]],
    total_minutes: int,
    date_str: str,
) -> bool:
    """日次サマリー通知。"""
    from datetime import date as date_cls
    d = date_cls.fromisoformat(date_str)
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    label = f"{d.month}月{d.day}日（{weekdays[d.weekday()]}）"

    if not periods:
        msg = f"📊 着席サマリー {label}\n\n着席記録なし"
    else:
        lines = [f"📊 着席サマリー {label}\n"]
        for start, end, minutes in periods:
            h, m = divmod(minutes, 60)
            duration = f"{h}時間{m}分" if h else f"{m}分"
            lines.append(f"{start} 〜 {end}  （{duration}）")
        th, tm = divmod(total_minutes, 60)
        total_str = f"{th}時間{tm}分" if th else f"{tm}分"
        lines.append(f"\n合計着席: {total_str}")
        msg = "\n".join(lines)

    return send_webhook(webhook_url, msg, username="ChairLogger Summary")
