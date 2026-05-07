#!/data/data/com.termux/files/usr/bin/bash
# 既存のmain.pyプロセスをkillしてから起動する
# 使い方: bash run.sh

cd "$(dirname "$0")"

# 既存プロセスを終了
pkill -f "python main.py" 2>/dev/null || true
pkill -f "python3 main.py" 2>/dev/null || true
sleep 1

python main.py
