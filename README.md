# Stock Earnings Agent

Telegram AI agent for US stock earnings and market updates: proactively pushes earnings reminders and a daily tech-stock market summary to subscribers, and answers questions in Traditional Chinese. Powered by Groq (`llama-3.3-70b-versatile`) with tool-use, deployed to AWS Lambda + API Gateway + SQS + DynamoDB via SAM.

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
  GROQ_API_KEY=xxx \
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

Three Lambdas (single Docker image, differentiated by `ImageConfig.Command`): `webhook` (10s) enqueues each Telegram update to SQS, which decouples Telegram's 60s timeout from Groq's tool-use loop; `processor` (300s) reads off the queue, runs the agent, and replies via the Telegram Bot API; `scheduler` runs on two daily EventBridge schedules (08:00 sync, 10:00 notify, Asia/Taipei) to push earnings reminders and the daily tech-stock market summary to subscribers. State lives in DynamoDB (on-demand billing); AWS SAM (`infra/template.yaml`) provisions all of it.
