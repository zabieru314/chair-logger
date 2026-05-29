#!/data/data/com.termux/files/usr/bin/bash
# =============================================
# 着席ロガー Termux:Boot 自動起動スクリプト（watchdog付き）
# =============================================
# 配置先: ~/.termux/boot/start_logger.sh
# 権限  : chmod +x ~/.termux/boot/start_logger.sh
#
# 動作:
#   1. termux-wake-lock を取得（CPUスリープ防止）
#   2. main.py を起動し、終了したら自動再起動（watchdog）
#   3. ログは logs/boot.log に追記

PROJECT_DIR="$HOME/chair_logger/chair_logger"
PYTHON_BIN="python"
LOG_DIR="$PROJECT_DIR/logs"
BOOT_LOG="$LOG_DIR/boot.log"
RESTART_DELAY=15  # 再起動待機秒数

mkdir -p "$LOG_DIR"

echo "" >> "$BOOT_LOG"
echo "========================================" >> "$BOOT_LOG"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] start_logger.sh 起動（watchdog mode）" >> "$BOOT_LOG"

if [ ! -d "$PROJECT_DIR" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: プロジェクトディレクトリが見つかりません: $PROJECT_DIR" >> "$BOOT_LOG"
    exit 1
fi

if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] termux-wake-lock 取得" >> "$BOOT_LOG"
fi

cd "$PROJECT_DIR"

# watchdogループ: main.pyが終了したら自動再起動
RESTART_COUNT=0
while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] main.py 起動 (再起動回数: $RESTART_COUNT)" >> "$BOOT_LOG"
    "$PYTHON_BIN" main.py >> "$BOOT_LOG" 2>&1
    EXIT_CODE=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] main.py 終了 (exit=$EXIT_CODE)。${RESTART_DELAY}秒後に再起動..." >> "$BOOT_LOG"
    RESTART_COUNT=$((RESTART_COUNT + 1))
    sleep "$RESTART_DELAY"
done
