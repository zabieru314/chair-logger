"""
Flask Webサーバ

DBから直近の履歴を取得して、シンプルなテーブルで表示する。
書き込みはセンサースレッドが担当し、Web側は読み取りのみ。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

from flask import Flask, jsonify, render_template

from src.core.sensor import SensorMonitor
from src.db import models as db_models

logger = logging.getLogger(__name__)


def create_app(db_path: str, monitor: Optional[SensorMonitor] = None) -> Flask:
    """
    Flask アプリを生成する。

    Args:
        db_path: SQLite DB のパス
        monitor: SensorMonitor インスタンス（任意。リアルタイム状態表示に使用）
    """
    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)

    @app.route("/")
    def index():
        try:
            logs = db_models.fetch_recent_logs(db_path, limit=20)
            latest = db_models.fetch_latest_status(db_path)
            current_status = latest["status"] if latest else "unknown"

            monitor_state = monitor.get_current_state() if monitor else None

            return render_template(
                "index.html",
                logs=logs,
                current_status=current_status,
                monitor_state=monitor_state,
                generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception as e:
            logger.exception(f"index 表示で例外: {e}")
            return f"Internal error: {e}", 500

    @app.route("/api/status")
    def api_status():
        """JSONで現在の状態を返す（外部監視用）。"""
        try:
            latest = db_models.fetch_latest_status(db_path)
            monitor_state = monitor.get_current_state() if monitor else None
            return jsonify(
                {
                    "latest": latest,
                    "monitor": monitor_state,
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
        except Exception as e:
            logger.exception(f"api_status で例外: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/logs")
    def api_logs():
        """直近のログをJSON配列で返す。"""
        try:
            logs = db_models.fetch_recent_logs(db_path, limit=50)
            return jsonify(logs)
        except Exception as e:
            logger.exception(f"api_logs で例外: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "ok"}), 200

    return app
