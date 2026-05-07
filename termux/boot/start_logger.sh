#!/data/data/com.termux/files/usr/bin/sh
# =============================================
# 着席ロガー Termux:Boot 自動起動スクリプト
# =============================================
# 配置先: ~/.termux/boot/start_logger.sh
# 権限  : chmod +x ~/.termux/boot/start_logger.sh
#
# 動作:
#   1. termux-wake-lock を取得（端末がスリープしてもプロセスを維持）
#   2. プロジェクトディレクトリへ移動
#   3. main.py を nohup でバックグラウンド起動
#   4. ログは logs/boot.log に追記

set -eu

# ---------------------------------------------
# 設定（必要に応じて編集）
# ---------------------------------------------
PROJECT_DIR="$HOME/chair-logger"
PYTHON_BIN="python3"
LOG_DIR="$PROJECT_DIR/logs"
BOOT_LOG="$LOG_DIR/boot.log"
PID_FILE="$LOG_DIR/main.pid"

# ---------------------------------------------
# 事前準備
# ---------------------------------------------
mkdir -p "$LOG_DIR"

{
    echo "----------------------------------------"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] start_logger.sh 起動"
    echo "PROJECT_DIR=$PROJECT_DIR"
} >> "$BOOT_LOG" 2>&1

# 既に起動中なら何もしない
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 既に起動中 PID=$OLD_PID。何もしません。" >> "$BOOT_LOG"
        exit 0
    fi
fi

# ---------------------------------------------
# WakeLock（スリープ防止）
# ---------------------------------------------
if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock || true
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] termux-wake-lock 取得" >> "$BOOT_LOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] termux-wake-lock コマンド未検出" >> "$BOOT_LOG"
fi

# ---------------------------------------------
# プロジェクトディレクトリ確認
# ---------------------------------------------
if [ ! -d "$PROJECT_DIR" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] プロジェクトディレクトリが見つかりません: $PROJECT_DIR" >> "$BOOT_LOG"
    exit 1
fi

cd "$PROJECT_DIR"

# ---------------------------------------------
# main.py 起動
# ---------------------------------------------
nohup "$PYTHON_BIN" main.py >> "$BOOT_LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] main.py 起動 PID=$NEW_PID" >> "$BOOT_LOG"

exit 0
