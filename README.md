# Stock Earnings Agent

Telegram AI agent that proactively notifies users about US stock earnings calls and answers questions in Traditional Chinese. Powered by Google Gemini (`gemini-2.5-flash`) with tool-use, deployed to AWS Lambda + API Gateway + SQS + DynamoDB via SAM.

## Quick start

```bash
# 1. Local dev
cp .env.example .env  # fill in keys
uv sync
make test
make dev              # starts FastAPI on :8080, point ngrok at it
curl 'http://127.0.0.1:8080/earnings?date_from=2026-07-20&date_to=2026-07-24&symbol=AAPL'

# 2. Set SSM params (one-time, in AWS account)
make ssm-put \
  FINNHUB_API_KEY=xxx \
  GEMINI_API_KEY=xxx \
  TELEGRAM_BOT_TOKEN=xxx \
  TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 32)

# 3. Deploy
make build
make deploy GUIDED=1   # first time only

# 4. Wire Telegram to the API Gateway URL printed by the deploy
make set-webhook \
  TELEGRAM_BOT_TOKEN=xxx \
  WEBHOOK_URL=https://xxx.execute-api.ap-northeast-1.amazonaws.com/webhook \
  TELEGRAM_WEBHOOK_SECRET=$(aws ssm get-parameter --name /earnings-agent/telegram-webhook-secret --with-decryption --query Parameter.Value --output text)
```

## Architecture

See [plan.md](plan.md) for the design rationale. Webhook → SQS → Processor decouples Telegram's 60s timeout from Gemini's tool-use loop.
