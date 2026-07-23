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

Telegram bot AI agent for US stock earnings, responding in Traditional Chinese. Uses Groq (`llama-3.3-70b-versatile`) with tool-use loop via the `groq` SDK.

**Request flow:** Telegram webhook → `webhook_handler` (Lambda, 10s) → SQS → `processor_handler` (Lambda, 300s) → `run_agent()` → Groq tool-use loop (max 10 iterations) → Telegram reply. The SQS decoupling prevents Telegram's 60s timeout from killing long agent runs.

**Third Lambda:** `scheduler_handler` fires daily via EventBridge to push earnings reminders to subscribers.

**Key modules under `src/agent/`:**
- `agent.py` — Groq tool-use loop with function calling; `run_agent()` is the core entry point
- `tools/` — Four tools: `get_earnings_calendar`, `get_analyst_estimates`, `get_stock_news`, `get_quote` (all hit Finnhub API). Tool schemas are defined in each module and aggregated in `tools/__init__.py` as `ALL_TOOL_SCHEMAS` / `TOOL_FUNCTIONS`
- `db/` — Storage access behind a `StorageBackend` Protocol (`db/backend.py`) with two implementations selected via `STORAGE_BACKEND`: `DynamoDBBackend` (boto3 resource API, `@lru_cache` table singletons — `conversations`, `subscribers`, `dedup`) and `LocalSQLiteBackend` (local dev, with extra local-only tables: `earnings_calendar`, `news`, `market_summaries`, `company_summaries`, `earnings_notifications`, `watchlist`)
- `config.py` — Reads from SSM Parameter Store in Lambda (`/earnings-agent/` prefix), falls back to env vars locally. All accessors are `@lru_cache`
- `telegram_client.py` — Telegram Bot API wrapper; auto-chunks long messages at 4000 chars, falls back to plain text on Markdown parse failure
- `local_server.py` — FastAPI dev server that simulates the webhook→processor flow synchronously (no real SQS needed)

**Tool result contract:** `_run_tool()` wraps non-dict results (list, str) as `{"result": ...}` so the tool result is always a dict, which is then `json.dumps`-serialized into the Groq `tool` message content. New tools returning `list` or `str` are handled automatically; only `dict` passes through unwrapped.

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
| Groq API (`llama-3.3-70b-versatile`, free tier) | ~$0 |
| Finnhub API (free tier) + Telegram Bot | $0 |
| **合計** | **~$0/月（皆在免費額度內）** |

## Testing

Tests use `moto` to mock AWS services (DynamoDB, SQS) and `pytest-mock` for Groq API / Finnhub calls. Fixtures in `tests/conftest.py` auto-create all tables and queues. Tests never hit real AWS or external APIs.

## Environment Variables

See `.env.example`. Required keys: `GROQ_API_KEY`, `FINNHUB_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`. In production these come from SSM Parameter Store under `/earnings-agent/` prefix. Table names and queue URL have env var fallbacks for local dev.

## Coding Guidelines

- State assumptions explicitly before implementing. If multiple interpretations exist, present them.
- Minimum code that solves the problem. No speculative features, abstractions for single-use code, or error handling for impossible scenarios.
- Surgical changes only: don't "improve" adjacent code. Every changed line should trace to the user's request. Remove only orphans YOUR changes created.
- Define verifiable success criteria before starting. For bug fixes, write a reproducing test first.

### Harness Workflow

開發任務預設走 harness skills（docs/ 骨架已由 `harness-init` 建立）：

- **`feature` skill** — 方向已討論清楚的小功能、修 bug、補測試。流程：generator（實作 + 測試）→ evaluator（驗收 + QA report 寫入 `docs/qa-reports/`、追加 `docs/CHANGELOG.md`）。不自動 commit/PR。
- **`sprint` skill** — 新功能或需要 spec/contract 的大改動。流程：planner（`docs/specs/` + `docs/contracts/`，必要時 ADR）→ generator → evaluator → 自動 commit、push、開 PR。
- 只有純問答、trivial 一行修改、或使用者明確要求直接改時才跳過 harness。
- **實作與驗證一律經 feature/sprint skill dispatch 的 generator/evaluator**，不要直接派其他 agent 執行開發任務。全域的 `infra-engineer`（部署執行、部署後驗證）與 `monitoring-engineer`（線上診斷、observability 設計）只負責 harness 之外的維運工作；它們發現需要改 code 或 IaC 時，回到 harness 流程處理。

## Deployment & Ops

Region `ap-northeast-1`、CloudFormation stack `earnings-agent`。Lambda：`earnings-agent-webhook` / `-processor` / `-scheduler`。部署後驗證、監控重點、已知陷阱與 incident 排查步驟見 `docs/runbook.md`。

## Project Status

### Done
- [x] Telegram 對話（繁體中文回應）
- [x] 4 Tools：earnings calendar、analyst estimates、stock news、quote
- [x] 多輪對話（DynamoDB 持久化，20 筆上限，30 天 TTL）
- [x] SQS 解耦（webhook → SQS → processor）
- [x] Telegram update dedup
- [x] SAM deploy（Lambda + API Gateway + SQS + EventBridge）

