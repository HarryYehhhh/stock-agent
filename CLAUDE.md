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

Telegram bot AI agent for US stock earnings, responding in Traditional Chinese. Uses Google Gemini (`gemini-2.5-flash`) with tool-use loop via `google-genai` SDK.

**Request flow:** Telegram webhook → `webhook_handler` (Lambda, 10s) → SQS → `processor_handler` (Lambda, 300s) → `run_agent()` → Gemini tool-use loop (max 10 iterations) → Telegram reply. The SQS decoupling prevents Telegram's 60s timeout from killing long agent runs.

**Third Lambda:** `scheduler_handler` fires daily via EventBridge to push earnings reminders to subscribers.

**Key modules under `src/agent/`:**
- `agent.py` — Gemini tool-use loop with function calling; `run_agent()` is the core entry point
- `tools/` — Three tools: `get_earnings_calendar`, `get_analyst_estimates`, `get_stock_news` (all hit Finnhub API). Tool schemas are defined in each module and aggregated in `tools/__init__.py` as `ALL_TOOL_SCHEMAS` / `TOOL_FUNCTIONS`
- `db/` — DynamoDB access via boto3 resource API with `@lru_cache` table singletons: `conversations` (chat history, 20-message limit, 30-day TTL), `subscribers` (push notifications), `dedup` (Telegram update deduplication via conditional put)
- `config.py` — Reads from SSM Parameter Store in Lambda (`/earnings-agent/` prefix), falls back to env vars locally. All accessors are `@lru_cache`
- `telegram_client.py` — Telegram Bot API wrapper; auto-chunks long messages at 4000 chars, falls back to plain text on Markdown parse failure
- `local_server.py` — FastAPI dev server that simulates the webhook→processor flow synchronously (no real SQS needed)

**Tool result contract:** `_run_tool()` wraps non-dict results (list, str) as `{"result": ...}` because Gemini's `FunctionResponse` requires a dict. New tools returning `list` or `str` are handled automatically; only `dict` passes through unwrapped.

**Processor routing** (`processor_handler.py`): `/start`, `/help` → welcome message; `/subscribe`, `/unsubscribe` → manage push notifications; anything else → `run_agent()` with conversation history.

**AWS resources (SAM `infra/template.yaml`):** API Gateway HTTP API, 3 Lambda functions (Docker container images from single Dockerfile, differentiated by `ImageConfig.Command`), 3 DynamoDB tables (on-demand billing, TTL on conversations + dedup), SQS queue + DLQ (3 retries, 14-day retention), EventBridge daily schedule.

## DynamoDB Schema

| Table | PK | SK | TTL | 說明 |
|-------|----|----|-----|------|
| `earnings-agent-conversations` | `chat_id` (S) | `timestamp` (S) | `ttl` (30 天) | 對話紀錄 |
| `earnings-agent-subscribers` | `chat_id` (S) | — | — | 訂閱者（含 `active` flag） |
| `earnings-agent-processed-updates` | `update_id` (N) | — | `ttl` (1 小時) | Telegram update dedup |

## Cost Estimate

| 元件 | 費用 |
|------|------|
| API Gateway + Lambda + EventBridge + DynamoDB + SQS + ECR | 免費額度內 ~$0 |
| Gemini API (`gemini-2.5-flash`) | ~$1-5/月 |
| Finnhub API (free tier) + Telegram Bot | $0 |
| **合計** | **~$1-5/月** |

## Testing

Tests use `moto` to mock AWS services (DynamoDB, SQS) and `pytest-mock` for Gemini API / Finnhub calls. Fixtures in `tests/conftest.py` auto-create all tables and queues. Tests never hit real AWS or external APIs.

## Environment Variables

See `.env.example`. Required keys: `GEMINI_API_KEY`, `FINNHUB_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`. In production these come from SSM Parameter Store under `/earnings-agent/` prefix. Table names and queue URL have env var fallbacks for local dev.

## Coding Guidelines

- State assumptions explicitly before implementing. If multiple interpretations exist, present them.
- Minimum code that solves the problem. No speculative features, abstractions for single-use code, or error handling for impossible scenarios.
- Surgical changes only: don't "improve" adjacent code. Every changed line should trace to the user's request. Remove only orphans YOUR changes created.
- Define verifiable success criteria before starting. For bug fixes, write a reproducing test first.

## Project Status

### Done
- [x] Telegram 對話（繁體中文回應）
- [x] 3 Tools：earnings calendar、analyst estimates、stock news
- [x] 多輪對話（DynamoDB 持久化，20 筆上限，30 天 TTL）
- [x] SQS 解耦（webhook → SQS → processor）
- [x] Telegram update dedup
- [x] SAM deploy（Lambda + API Gateway + SQS + EventBridge）

### TODO
- [ ] 每天 08:00 推播隔天財報提醒（scheduler_handler）
- [ ] `/subscribe` 與 `/unsubscribe` 指令

### Phase 2
- [ ] 用戶自訂 watchlist（`/watch`、`/unwatch`、`/mylist`）
- [ ] 股價即時查詢 tool（`get_quote`）
- [ ] 財報電話會議逐字稿摘要
- [ ] 技術指標 tool（RSI、MA、布林通道）
- [ ] CloudWatch Dashboard 監控
