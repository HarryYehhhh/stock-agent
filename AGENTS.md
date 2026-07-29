# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Build & Dev Commands

```bash
make install          # uv sync
make test             # PYTHONPATH=src uv run pytest tests/ -v
PYTHONPATH=src uv run pytest tests/test_local_integration.py -v  # integration: local SQLite + in-process Finnhub HTTP stub, no real external APIs
make dev              # FastAPI local server on :8080 (needs .env from .env.example)
make build            # cd infra && sam build
make deploy           # SAM deploy to AWS (add GUIDED=1 for first time)
make logs-webhook     # tail WebhookFunction CloudWatch logs
make logs-processor   # tail ProcessorFunction CloudWatch logs
make logs-scheduler   # tail SchedulerFunction CloudWatch logs
```

Run a single test: `PYTHONPATH=src uv run pytest tests/test_agent.py -v -k test_name`

## Architecture

Telegram bot AI agent for US stock earnings, responding in Traditional Chinese. Uses Groq (`llama-3.3-70b-versatile`) with tool-use loop via the `groq` SDK.

**Request flow:** Telegram webhook → `webhook_handler` (Lambda, 10s) → SQS → `processor_handler` (Lambda, 300s) → `run_agent()` → Groq tool-use loop (max 10 iterations) → Telegram reply. The SQS decoupling prevents Telegram's 60s timeout from killing long agent runs.

**Third Lambda:** `scheduler_handler` fires daily via EventBridge to push earnings reminders to subscribers.

**Key modules under `src/agent/`:**
- `agent.py` — Groq tool-use loop with function calling; `run_agent()` is the core entry point
- `tools/` — Five tools: `get_earnings_calendar`, `get_stock_news`, `get_quote` (hit Finnhub API), `get_market_summary` (DB cache hit skips Finnhub; cache miss also hits Finnhub via `get_quote` for index quotes), `search_knowledge_base` (reads local `knowledge/*.md`, no external call). Tool schemas are defined in each module and aggregated in `tools/__init__.py` as `ALL_TOOL_SCHEMAS` / `TOOL_FUNCTIONS`
- `db/` — DynamoDB access via boto3 resource API with `@lru_cache` table singletons: `conversations` (chat history, 20-message limit, 30-day TTL), `subscribers` (push notifications), `dedup` (Telegram update deduplication via conditional put)
- `config.py` — Reads from SSM Parameter Store in Lambda (`/earnings-agent/` prefix), falls back to env vars locally. All accessors are `@lru_cache`
- `telegram_client.py` — Telegram Bot API wrapper; auto-chunks long messages at 4000 chars, falls back to plain text on Markdown parse failure
- `local_server.py` — FastAPI dev server that simulates the webhook→processor flow synchronously (no real SQS needed)

**Processor routing** (`processor_handler.py`): `/start`, `/help` → welcome message; `/subscribe`, `/unsubscribe` → manage push notifications; anything else → `run_agent()` with conversation history.

**AWS resources (SAM `infra/template.yaml`):** API Gateway HTTP API, 3 Lambda functions (Docker container images from single Dockerfile, differentiated by `ImageConfig.Command`), 3 DynamoDB tables (on-demand billing, TTL on conversations + dedup), SQS queue + DLQ (3 retries, 14-day retention), EventBridge daily schedule.

## Testing

Tests use `moto` to mock AWS services (DynamoDB, SQS) and `pytest-mock` for Groq API / Finnhub calls. Fixtures in `tests/conftest.py` auto-create all tables and queues. Tests never hit real AWS or external APIs.

Integration tests that cross the local SQLite/filesystem boundary and Finnhub HTTP client boundary live in `tests/test_local_integration.py`. They use the `finnhub_stub` fixture, an in-process `ThreadingHTTPServer`, to assert outbound method/path/query and return controlled Finnhub responses. Do not call the real Finnhub API in tests.

## Environment Variables

See `.env.example`. Required keys: `GROQ_API_KEY`, `FINNHUB_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`. In production these come from SSM Parameter Store under `/earnings-agent/` prefix. Table names and queue URL have env var fallbacks for local dev.

## Coding Guidelines

- State assumptions explicitly before implementing. If multiple interpretations exist, present them.
- Minimum code that solves the problem. No speculative features, abstractions for single-use code, or error handling for impossible scenarios.
- Surgical changes only: don't "improve" adjacent code. Every changed line should trace to the user's request. Remove only orphans YOUR changes created.
- Define verifiable success criteria before starting. For bug fixes, write a reproducing test first.
