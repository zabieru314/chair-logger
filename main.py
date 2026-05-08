"""
エントリーポイント

役割:
- .env 読み込み
- ロギング設定
- DB初期化
- SensorMonitor 起動（センサー監視 + 温度監視）
- Flask Webサーバー起動

Termux:Boot 経由で nohup 起動されることを想定。
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

# プロジェクトルートを sys.path に登録（src パッケージ解決のため）
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.sensor import SensorConfig, SensorMonitor  # noqa: E402
from src.db import models as db_models  # noqa: E402
from src.utils import notifier  # noqa: E402
from src.web.app import create_app  # noqa: E402


# ---------------------------------------------------------------------------
# ロギングセットアップ
# ---------------------------------------------------------------------------

def kill_by_pidfile(pid_file: str) -> None:
    """PIDファイルに記録された前回のプロセスをkillする。"""
    try:
        if not os.path.exists(pid_file):
            return
        with open(pid_file) as f:
            pid = int(f.read().strip())
        if pid == os.getpid():
            return
        try:
            os.kill(pid, signal.SIGKILL)
            print(f"[INFO] 前回のプロセス PID={pid} をkillしました（PIDファイル）")
            time.sleep(1)
        except ProcessLookupError:
            pass
    except Exception as e:
        print(f"[WARN] PIDファイルkill失敗: {e}")


def kill_by_ps(script_name: str) -> None:
    """ps で同名スクリプトの旧プロセスをkillする（停止状態も含む）。"""
    import re
    import subprocess
    try:
        result = subprocess.run(
            ["ps", "-ef"],
            capture_output=True, text=True, timeout=5
        )
        my_pid = os.getpid()
        for line in result.stdout.splitlines():
            if script_name in line and "grep" not in line:
                m = re.search(r'\s+(\d+)\s+', line)
                if m:
                    pid = int(m.group(1))
                    if pid != my_pid:
                        os.kill(pid, signal.SIGKILL)
                        print(f"[INFO] 旧プロセス PID={pid} をkillしました（ps）")
                        time.sleep(1)
    except Exception as e:
        print(f"[WARN] psコマンドkill失敗: {e}")


def write_pidfile(pid_file: str) -> None:
    """現在のPIDをファイルに書き込む。"""
    try:
        os.makedirs(os.path.dirname(pid_file), exist_ok=True)
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        print(f"[WARN] PIDファイル書き込み失敗: {e}")


def setup_logging(log_path: str) -> None:
    log_dir = os.path.dirname(log_path)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    handlers: list[logging.Handler] = []

    # ファイル（ローテート）
    try:
        file_handler = RotatingFileHandler(
            log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
        )
        handlers.append(file_handler)
    except Exception as e:
        print(f"[WARN] ファイルロガー初期化失敗: {e}", file=sys.stderr)

    # コンソール
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    )
    handlers.append(stream_handler)

    logging.basicConfig(
        level=logging.INFO,
        handlers=handlers,
        force=True,
    )


# ---------------------------------------------------------------------------
# 設定読み込み
# ---------------------------------------------------------------------------

def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_config() -> tuple[SensorConfig, dict]:
    """環境変数から SensorConfig と Flask用設定を構築する。"""
    db_path = os.getenv("DB_PATH", "data/chair_log.db")
    if not os.path.isabs(db_path):
        db_path = str(PROJECT_ROOT / db_path)

    sensor_cfg = SensorConfig(
        db_path=db_path,
        webhook_url=os.getenv("WEBHOOK_URL") or None,
        variance_threshold=_get_float("VARIANCE_THRESHOLD", 0.05),
        debounce_delay_sec=_get_float("DEBOUNCE_DELAY_SEC", 5.0),
        debounce_left_sec=_get_float("DEBOUNCE_LEFT_SEC", 5.0),
        max_temp_celsius=_get_float("MAX_TEMP_CELSIUS", 40.0),
        cooldown_sleep_sec=_get_int("COOLDOWN_SLEEP_SEC", 300),
        temp_check_interval_sec=_get_float("TEMP_CHECK_INTERVAL_SEC", 60.0),
        sensor_poll_interval_sec=_get_int("SENSOR_POLL_INTERVAL_SEC", 30),
    )

    web_cfg = {
        "host": os.getenv("FLASK_HOST", "0.0.0.0"),
        "port": _get_int("FLASK_PORT", 8080),
    }
    return sensor_cfg, web_cfg


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    # .env 読み込み
    load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

    log_path = os.getenv("LOG_PATH", "logs/app.log")
    if not os.path.isabs(log_path):
        log_path = str(PROJECT_ROOT / log_path)
    setup_logging(log_path)

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("着席ロガー 起動")
    logger.info("=" * 60)

    sensor_cfg, web_cfg = load_config()

    logger.info(
        f"設定: db={sensor_cfg.db_path} "
        f"variance_threshold={sensor_cfg.variance_threshold} "
        f"debounce={sensor_cfg.debounce_delay_sec}s "
        f"sensor_interval={sensor_cfg.sensor_interval_ms}ms "
        f"max_temp={sensor_cfg.max_temp_celsius}℃ "
        f"cooldown={sensor_cfg.cooldown_sleep_sec}s"
    )

    # DB初期化
    try:
        db_models.init_db(sensor_cfg.db_path)
    except Exception as e:
        logger.exception(f"DB初期化失敗: {e}")
        return 1

    # SensorMonitor 起動
    monitor = SensorMonitor(sensor_cfg)
    monitor.start()

    # 起動通知（失敗しても続行）
    try:
        notifier.notify_startup(sensor_cfg.webhook_url)
    except Exception:
        logger.exception("起動通知で例外（続行）")

    # 終了シグナル
    stop_event = threading.Event()

    def _shutdown(signum, frame):
        logger.info(f"シグナル {signum} を受信。終了処理開始")
        stop_event.set()
        monitor.stop()

    try:
        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)
    except ValueError:
        # メインスレッド以外では登録できない場合がある（無視）
        pass

    # 前回のプロセスをkillしてからFlask起動（PIDファイル→ssの順で試みる）
    pid_file = str(PROJECT_ROOT / "logs" / "main.pid")
    kill_by_pidfile(pid_file)
    kill_by_ps("main.py")
    write_pidfile(pid_file)
    app = create_app(sensor_cfg.db_path, monitor=monitor)
    try:
        logger.info(f"Flask 起動: http://{web_cfg['host']}:{web_cfg['port']}/")
        app.run(
            host=web_cfg["host"],
            port=web_cfg["port"],
            debug=False,
            use_reloader=False,
            threaded=True,
        )
    except Exception as e:
        logger.exception(f"Flask 起動で例外: {e}")
    finally:
        monitor.stop()
        logger.info("着席ロガー 終了")

    return 0


if __name__ == "__main__":
    sys.exit(main())
