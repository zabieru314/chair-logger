"""
DB操作モジュール（SQLite）

スレッドセーフ設計のポイント:
- sqlite3 のコネクションはデフォルトで作成スレッド以外からは扱えないため、
  アクセスごとに新規コネクションを生成する（短命コネクション戦略）。
- さらに WAL モードを有効化して、書き込み中の読み込みブロックを最小化する。
- 並行書き込みが極端に増えると壊れるが、本システムは
  「センサー側1スレッドの書き込み + Web側の読み込み」だけなので、
  この戦略で十分安全に動作する。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

# 書き込みは1本にまとめるためのプロセス内ロック
_WRITE_LOCK = threading.Lock()


@contextmanager
def _connect(db_path: str) -> Iterator[sqlite3.Connection]:
    """短命コネクションを生成するコンテキストマネージャ。"""
    conn = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        # WAL モードで読み書き並行性を改善
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            logger.exception("SQLite コネクションの close で例外")


def init_db(db_path: str) -> None:
    """
    DBファイルとテーブルを初期化する。

    chair_log テーブル:
        id          : 主キー
        timestamp   : ISO8601 文字列（ローカル時刻）
        status      : 'seated' | 'left'
        z_value     : 確定時の重力Z値（参考用）
        note        : 任意メモ
    """
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    with _WRITE_LOCK, _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chair_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT    NOT NULL,
                status    TEXT    NOT NULL CHECK (status IN ('seated', 'left')),
                z_value   REAL,
                note      TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chair_log_timestamp ON chair_log(timestamp)"
        )
        logger.info(f"DB初期化完了: {db_path}")


def insert_status(
    db_path: str,
    status: str,
    z_value: Optional[float] = None,
    note: Optional[str] = None,
) -> bool:
    """
    着席/離席ステータスをINSERTする。

    Args:
        status: 'seated' または 'left'
    Returns:
        成功 True / 失敗 False
    """
    if status not in ("seated", "left"):
        logger.error(f"不正なステータス値: {status!r}")
        return False

    timestamp = datetime.now().isoformat(timespec="seconds")

    try:
        with _WRITE_LOCK, _connect(db_path) as conn:
            conn.execute(
                "INSERT INTO chair_log (timestamp, status, z_value, note) VALUES (?, ?, ?, ?)",
                (timestamp, status, z_value, note),
            )
        logger.info(f"DBに記録: {timestamp} {status} z={z_value}")
        return True
    except sqlite3.Error as e:
        logger.error(f"DB書き込み失敗: {e}")
        return False
    except Exception as e:
        logger.exception(f"DB書き込みで予期しない例外: {e}")
        return False


def fetch_recent_logs(db_path: str, limit: int = 20) -> list[dict]:
    """
    直近の履歴を新しい順で取得する。
    """
    try:
        with _connect(db_path) as conn:
            cursor = conn.execute(
                "SELECT id, timestamp, status, z_value, note "
                "FROM chair_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        logger.error(f"DB読み込み失敗: {e}")
        return []
    except Exception as e:
        logger.exception(f"DB読み込みで予期しない例外: {e}")
        return []


def fetch_today_logs(db_path: str, date_str: str) -> list[dict]:
    """指定日（YYYY-MM-DD）の全ログを時刻昇順で取得する。"""
    try:
        with _connect(db_path) as conn:
            cursor = conn.execute(
                "SELECT timestamp, status FROM chair_log "
                "WHERE timestamp LIKE ? ORDER BY timestamp ASC",
                (f"{date_str}%",),
            )
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        logger.error(f"DB読み込み失敗: {e}")
        return []
    except Exception as e:
        logger.exception(f"DB読み込みで予期しない例外: {e}")
        return []


def calc_daily_summary(
    db_path: str, date_str: str, end_dt: datetime
) -> tuple[list[tuple[str, str, int]], int]:
    """
    指定日の着席期間リストと合計分数を返す。

    Returns:
        periods: [(開始時刻文字列, 終了時刻文字列, 分数), ...]
        total_minutes: 合計着席分数
    """
    logs = fetch_today_logs(db_path, date_str)
    periods: list[tuple[str, str, int]] = []
    total_sec = 0
    seated_start: Optional[datetime] = None

    for row in logs:
        dt = datetime.fromisoformat(row["timestamp"])
        if row["status"] == "seated":
            seated_start = dt
        elif row["status"] == "left" and seated_start is not None:
            sec = (dt - seated_start).total_seconds()
            periods.append((
                seated_start.strftime("%H:%M"),
                dt.strftime("%H:%M"),
                int(sec // 60),
            ))
            total_sec += sec
            seated_start = None

    # まだ着席中の場合: end_dt までカウント
    if seated_start is not None:
        sec = (end_dt - seated_start).total_seconds()
        periods.append((
            seated_start.strftime("%H:%M"),
            end_dt.strftime("%H:%M") + "〜",
            int(sec // 60),
        ))
        total_sec += sec

    return periods, int(total_sec // 60)


def fetch_latest_status(db_path: str) -> Optional[dict]:
    """
    最新の1件を取得する。レコードが無ければ None。
    """
    try:
        with _connect(db_path) as conn:
            cursor = conn.execute(
                "SELECT id, timestamp, status, z_value, note "
                "FROM chair_log ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        logger.error(f"DB読み込み失敗: {e}")
        return None
    except Exception as e:
        logger.exception(f"DB読み込みで予期しない例外: {e}")
        return None
