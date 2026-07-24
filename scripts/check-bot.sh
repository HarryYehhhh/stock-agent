#!/bin/bash
# make check-bot 的 wrapper：載入 .env 後跑一次性健檢。
#
# 純讀取檢查（本地 server / ngrok tunnel / Telegram webhook / 金鑰 / Groq），
# 不會寫入 DB 或發送 Telegram 訊息。與 run-daily.sh 一樣，先 source .env
# 因為金鑰只在 .env 裡，環境變數不會自動存在。

cd "$(dirname "$0")/.."

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

PYTHONPATH=src uv run python -m agent.check_bot
