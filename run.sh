#!/data/data/com.termux/files/usr/bin/bash
# 起動スクリプト（watchdog付き: 落ちたら自動再起動）
# 使い方: bash run.sh

cd "$(dirname "$0")"

# 既存プロセスを終了
pkill -f "python main.py" 2>/dev/null || true
pkill -f "python3 main.py" 2>/dev/null || true
sleep 1

# termux-wake-lockでCPUスリープ防止
if command -v termux-wake-lock >/dev/null 2>&1; then
    termux-wake-lock
fi

echo "=== 着席ロガー watchdog 起動 ==="

RESTART_COUNT=0
while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] main.py 起動 (再起動回数: $RESTART_COUNT)"
    python main.py
    EXIT_CODE=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] main.py 終了 (exit=$EXIT_CODE)。15秒後に再起動..."
    RESTART_COUNT=$((RESTART_COUNT + 1))
    sleep 15
done
