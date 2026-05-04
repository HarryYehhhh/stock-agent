# Stock Earnings Agent

每日把隔天美股科技類股的財報日曆推播到單一 Telegram 帳號。Lambda 每天 00:00 UTC（08:00 Asia/Taipei）跑一次：抓 Finnhub earnings calendar、用 `/stock/profile2` 過濾出科技類股（Technology、Semiconductors、Software、Communications、Media、Telecommunication），再交給 Google Gemini (`gemini-2.5-flash`) 產生繁體中文摘要，最後送到 Telegram。

架構極簡：1 個 Lambda + EventBridge + ECR，AWS 端費用幾乎為 $0。

## Quick start

```bash
# 1. 安裝 + 跑測試
uv sync
make test

# 2. 設環境變數（4 個都必填）
export FINNHUB_API_KEY=...
export GEMINI_API_KEY=...
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...     # 你的數字 chat id

# 3. 部署
make build
make deploy GUIDED=1            # 首次部署用 guided
make deploy                     # 之後直接跑

# 4. 驗證（手動觸發一次）
make invoke-scheduler

# 5. 看日誌
make logs-scheduler
```

## Architecture

```
EventBridge (每日 00:00 UTC)
        ↓
SchedulerFunction (Lambda, 300s)
        ↓ Finnhub /calendar/earnings
        ↓ Finnhub /stock/profile2 (per symbol, filter to tech)
        ↓ Gemini tool-use loop (中文摘要)
        ↓ Telegram sendMessage
   TELEGRAM_CHAT_ID
```

詳細部署流程見 [docs/deployment-flow.md](docs/deployment-flow.md)。
