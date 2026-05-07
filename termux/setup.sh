#!/data/data/com.termux/files/usr/bin/bash
# 着席検知システム セットアップスクリプト
#
# 使い方:
#   bash termux/setup.sh "https://discord.com/api/webhooks/xxxx/yyyy" [DEBOUNCE秒数]
#
# 例（テスト用に5秒）:
#   bash termux/setup.sh "https://discord.com/api/webhooks/..." 5
# 例（本番用に60秒）:
#   bash termux/setup.sh "https://discord.com/api/webhooks/..." 60
#
# 第2引数を省略すると DEBOUNCE_DELAY_SEC=60（デフォルト）

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
WEBHOOK_URL="${1:-}"
DEBOUNCE_DELAY_SEC="${2:-60}"

echo "=== 着席検知システム セットアップ ==="
echo "プロジェクトルート: ${SCRIPT_DIR}"

# ---- .env 生成 ----
cat > "${ENV_FILE}" << EOF
WEBHOOK_URL=${WEBHOOK_URL}

Z_THRESHOLD=5.0
DEBOUNCE_DELAY_SEC=${DEBOUNCE_DELAY_SEC}
MAX_TEMP_CELSIUS=40.0
COOLDOWN_SLEEP_SEC=300
TEMP_CHECK_INTERVAL_SEC=60

FLASK_HOST=0.0.0.0
FLASK_PORT=8080

DB_PATH=data/chair_log.db
LOG_PATH=logs/app.log

SENSOR_INTERVAL_MS=1000
EOF

echo ".env を生成しました: ${ENV_FILE}"

# ---- 必要パッケージのインストール ----
echo ""
echo "=== パッケージインストール ==="
pkg install -y python termux-api 2>/dev/null || echo "[WARN] pkg install に失敗しました（スキップ）"

pip install -r "${SCRIPT_DIR}/requirements.txt" --quiet

# ---- Termux:Boot 用シェルスクリプトの配置 ----
BOOT_DIR="${HOME}/.termux/boot"
mkdir -p "${BOOT_DIR}"
cp "${SCRIPT_DIR}/termux/boot/start_logger.sh" "${BOOT_DIR}/start_logger.sh"
chmod +x "${BOOT_DIR}/start_logger.sh"
echo "Termux:Boot スクリプトを配置しました: ${BOOT_DIR}/start_logger.sh"

echo ""
echo "=== セットアップ完了 ==="
echo "次のコマンドで起動できます:"
echo "  cd ${SCRIPT_DIR} && python main.py"
