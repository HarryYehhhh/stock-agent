# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Dev Commands

```bash
make install          # uv sync
make test             # PYTHONPATH=src uv run pytest tests/ -v
make dev              # FastAPI local server on :8080 (needs .env from .env.example)
make build            # cd infra && sam build
make deploy           # SAM deploy to AWS (add GUIDED=1 for first time)
make logs-webhook     # tail WebhookFunction CloudWatch logs
make logs-processor   # tail ProcessorFunction CloudWatch logs
make logs-scheduler   # tail SchedulerFunction CloudWatch logs
```

Run a single test: `PYTHONPATH=src uv run pytest tests/test_agent.py -v -k test_name`

## Architecture

Telegram bot AI agent for US stock earnings, responding in Traditional Chinese. Uses Google Gemini (`gemini-2.5-flash`) with tool-use loop.

**Request flow:** Telegram webhook → `webhook_handler` (Lambda, 10s) → SQS → `processor_handler` (Lambda, 300s) → `run_agent()` → Gemini tool-use loop (max 10 iterations) → Telegram reply. The SQS decoupling prevents Telegram's 60s timeout from killing long agent runs.

**Third Lambda:** `scheduler_handler` fires daily via EventBridge to push earnings reminders to subscribers.

**Key modules under `src/agent/`:**
- `agent.py` — Gemini tool-use loop with function calling via `google-genai` SDK
- `tools/` — Four tools: `get_earnings_calendar`, `get_analyst_estimates`, `get_stock_news`, `get_stocks_by_sector` (all hit Finnhub API except sector which reads config)
- `db/` — DynamoDB access: `conversations` (chat history), `subscribers` (push notifications), `dedup` (Telegram update deduplication via conditional put)
- `config.py` — Reads from SSM Parameter Store in Lambda, falls back to env vars locally

**AWS resources (SAM `infra/template.yaml`):** API Gateway HTTP API, 3 Lambda functions (Docker container), 3 DynamoDB tables with TTL, SQS queue + DLQ, EventBridge daily schedule.

## Testing

Tests use `moto` to mock AWS services (DynamoDB, SQS) and `pytest-mock` for Gemini API / Finnhub calls. Fixtures are in `tests/conftest.py`.

## Environment Variables

See `.env.example`. Required keys: `GEMINI_API_KEY`, `FINNHUB_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`. In production these come from SSM Parameter Store under `/earnings-agent/` prefix.
