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

Telegram bot AI agent for US stock earnings, responding in Traditional Chinese. Uses Groq (`openai/gpt-oss-120b`) with tool-use loop via the `groq` SDK.

**Request flow:** Telegram webhook → `webhook_handler` (Lambda, 10s) → SQS → `processor_handler` (Lambda, 300s) → `run_agent()` → Groq tool-use loop (max 10 iterations) → Telegram reply. The SQS decoupling prevents Telegram's 60s timeout from killing long agent runs.

**Third Lambda:** `scheduler_handler` runs on two daily EventBridge schedules (Asia/Taipei): `{"mode": "sync"}` at 08:00 refreshes the earnings calendar and tech-stock/AI news caches; `{"mode": "notify"}` at 10:00 pushes watchlist earnings reminders (releasing within 24h) and the daily tech-stock market summary (`agent.market_summary.push_daily_summary`) to subscribers.

**Key modules under `src/agent/`:**
- `agent.py` — Groq tool-use loop with function calling; `run_agent()` is the core entry point
- `tools/` — Five tools: `get_earnings_calendar`, `get_stock_news`, `get_quote` (hit Finnhub API), `get_market_summary` (DB cache hit skips Finnhub; cache miss also hits Finnhub via `get_quote` for index quotes and each of the 12 tracked tech stocks — see `agent.market_summary.TECH_SYMBOLS`), `search_knowledge_base` (reads local `knowledge/*.md`, no external call). Tool schemas are defined in each module and aggregated in `tools/__init__.py` as `ALL_TOOL_SCHEMAS` / `TOOL_FUNCTIONS`
- `market_summary.py` — Daily tech-stock market summary: refreshes news/quotes for the 12 `TECH_SYMBOLS` from Finnhub, asks Groq to synthesize a Traditional Chinese briefing, and caches the result per (date, days_back, news set)
- `db/` — Storage access behind a `StorageBackend` Protocol (`db/backend.py`) with two implementations selected via `STORAGE_BACKEND`: `DynamoDBBackend` (boto3 resource API, table handles created once in `__init__` and reused via `get_backend()`'s `@lru_cache` singleton — covers all 11 DynamoDB tables below) and `LocalSQLiteBackend` (local dev, same tables plus a local-only `market_summaries` cache table; the DynamoDB backend instead piggybacks market-summary caching onto `NewsTable` under `bucket="market_summary"`)
- `config.py` — Reads from SSM Parameter Store in Lambda (`/earnings-agent/` prefix), falls back to env vars locally. All accessors are `@lru_cache`
- `telegram_client.py` — Telegram Bot API wrapper; auto-chunks long messages at 4000 chars, falls back to plain text on Markdown parse failure
- `local_server.py` — FastAPI dev server that simulates the webhook→processor flow synchronously (no real SQS needed)

**Tool result contract:** `_run_tool()` wraps non-dict results (list, str) as `{"result": ...}` so the tool result is always a dict, which is then `json.dumps`-serialized into the Groq `tool` message content. New tools returning `list` or `str` are handled automatically; only `dict` passes through unwrapped.

**Processor routing** (`processor_handler.py`): `/start`, `/help` → welcome message; `/subscribe`, `/unsubscribe` → manage push notifications (earnings reminders + daily market summary); `/watchlist`, `/watch <SYMBOL>`, `/unwatch <SYMBOL>` → manage the user's tracked-symbol watchlist; `/correct <text>` → save a user-submitted correction for maintainer review; anything else → `run_agent()` with conversation history.

**AWS resources (SAM `infra/template.yaml`):** API Gateway HTTP API, 3 Lambda functions (Docker container images from single Dockerfile, differentiated by `ImageConfig.Command`), 11 DynamoDB tables (on-demand billing, TTL on most tables — see schema below), SQS queue + DLQ (3 retries, 14-day retention), 2 EventBridge daily schedules (scheduler sync/notify), SNS alarm topic + 4 CloudWatch alarms (scheduler/processor errors, scheduler duration, DLQ messages).

## DynamoDB Schema

| Table | PK | SK | TTL | 說明 |
|-------|----|----|-----|------|
| `earnings-agent-conversations` | `chat_id` (S) | `timestamp` (S) | `ttl` (30 天) | 對話紀錄 |
| `earnings-agent-subscribers` | `chat_id` (S) | — | — | 訂閱者（含 `active` flag） |
| `earnings-agent-processed-updates` | `update_id` (N) | — | `ttl` (1 小時) | Telegram update dedup |
| `earnings-agent-rate-limits` | `chat_id` (S) | `window_key` (S) | `ttl` (分鐘窗 2 分鐘 / 日窗 26 小時) | 每 chat_id 分鐘窗（3 則）與日窗（15 則）計數 |
| `earnings-agent-watchlist` | `chat_id` (S) | `symbol` (S) | — | 使用者追蹤股票清單 |
| `earnings-agent-earnings-calendar` | `symbol` (S) | `earning_date` (S) | `ttl` (45 天) | 財報行事曆快取（GSI `earning-date-index`） |
| `earnings-agent-earnings-notifications` | `chat_id` (S) | `notify_key` (S) | `ttl` (30 天) | 財報提醒發送紀錄（去重用） |
| `earnings-agent-company-summaries` | `symbol` (S) | `earning_date` (S) | `ttl` (30 天) | 個股財報前瞻摘要快取（Groq 產出） |
| `earnings-agent-news` | `headline` (S) | — | `ttl` (14 天，摘要快取項目 2 天) | 科技股個股新聞 + AI 產業新聞快取（GSI `published-at-index`）；DynamoDB 版另借此表以 `bucket="market_summary"` 快取每日摘要 Groq 呼叫結果（local 版走獨立的 `market_summaries` 表） |
| `earnings-agent-traces` | `chat_id` (S) | `sk` (S) | `ttl` (30 天) | 對話 trace（fast-path 命中、工具呼叫、token 用量等，debug/分析用，GSI `trace-time-index`） |
| `earnings-agent-corrections` | `correction_id` (S) | — | `ttl` (90 天) | 使用者 `/correct` 提交的糾正內容，待維護者審核（GSI `status-index`） |

## Cost Estimate

| 元件 | 費用 |
|------|------|
| API Gateway + Lambda + EventBridge + DynamoDB + SQS + ECR | 免費額度內 ~$0 |
| Groq API (`openai/gpt-oss-120b`, free tier) | ~$0 |
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
- 開 PR 時一律掛 `auto-merge` label：checks 全綠後 Auto Merge workflow 會自動 squash merge，並 dispatch deploy workflow 完成部署（GITHUB_TOKEN 的 merge push 不會觸發 push workflow，deploy 靠 dispatch 補上）。
- **實作與驗證一律經 feature/sprint skill dispatch 的 generator/evaluator**，不要直接派其他 agent 執行開發任務。全域的 `infra-engineer`（部署執行、部署後驗證）與 `monitoring-engineer`（線上診斷、observability 設計）只負責 harness 之外的維運工作；它們發現需要改 code 或 IaC 時，回到 harness 流程處理。

## Deployment & Ops

Region `ap-northeast-1`、CloudFormation stack `earnings-agent`。Lambda：`earnings-agent-webhook` / `-processor` / `-scheduler`。部署後驗證、監控重點、已知陷阱與 incident 排查步驟見 `docs/runbook.md`。

## Project Status

### Done
- [x] Telegram 對話（繁體中文回應）
- [x] 5 Tools：earnings calendar、stock news、quote、market summary、knowledge base search
- [x] 多輪對話（DynamoDB 持久化，20 筆上限，30 天 TTL）
- [x] SQS 解耦（webhook → SQS → processor）
- [x] Telegram update dedup
- [x] SAM deploy（Lambda + API Gateway + SQS + EventBridge）
- [x] 每日科技股焦點市場摘要推播（12 檔 TECH_SYMBOLS 個股報價 + 新聞，取代原 Big 7）

