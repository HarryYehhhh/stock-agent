# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Dev Commands

```bash
make install            # uv sync
make test               # PYTHONPATH=src uv run pytest tests/ -v
make build              # cd infra && sam build
make deploy             # SAM deploy (requires 4 env vars; see Environment Variables)
make logs-scheduler     # tail SchedulerFunction CloudWatch logs
make invoke-scheduler   # manually trigger the daily push for verification
```

Run a single test: `PYTHONPATH=src uv run pytest tests/test_scheduler.py -v -k test_name`

## Architecture

Single-purpose daily push: every day at 00:00 UTC (08:00 Asia/Taipei), one Lambda fetches tomorrow's US earnings calendar from Finnhub, filters to tech-sector stocks, asks Gemini to summarise in Traditional Chinese, and pushes the result to a single Telegram chat.

**Flow:** EventBridge ScheduleV2 → `scheduler_handler` (Lambda, 300s) → Finnhub `/calendar/earnings` → per-symbol Finnhub `/stock/profile2` for industry filter → Gemini tool-use loop (`run_agent`) → Telegram `sendMessage`.

There is no webhook, no SQS, no DynamoDB, no API Gateway — the conversational bot was removed to eliminate AWS cost surface area. The recipient is hardcoded via the `TELEGRAM_CHAT_ID` env var.

**Key modules under `src/agent/`:**
- `scheduler_handler.py` — Lambda entry point. `_filter_tech` calls `get_company_industry` per symbol and keeps `{Technology, Semiconductors, Software, Communications, Media, Telecommunication}`; industry is attached to each entry so the prompt can label it.
- `agent.py` — Gemini tool-use loop with function calling; `run_agent()` is the core entry point. Used here for the Chinese summary; tools can fetch analyst estimates / news on demand.
- `tools/` — `get_earnings_calendar`, `get_analyst_estimates`, `get_stock_news` (LLM-callable), plus `get_company_industry` (internal, not in the schema). Schemas aggregated in `tools/__init__.py` as `ALL_TOOL_SCHEMAS` / `TOOL_FUNCTIONS`.
- `config.py` — Plain env-var accessors (`finnhub_api_key`, `gemini_api_key`, `telegram_bot_token`, `telegram_chat_id`). No SSM, no caching.
- `telegram_client.py` — Telegram Bot API wrapper; auto-chunks long messages at 4000 chars, falls back to plain text on Markdown parse failure.

**Tool result contract:** `_run_tool()` wraps non-dict results (list, str) as `{"result": ...}` because Gemini's `FunctionResponse` requires a dict. New tools returning `list` or `str` are handled automatically.

**AWS resources (SAM `infra/template.yaml`):** Just one — `SchedulerFunction` (Docker image, 1024 MB, 300s timeout) with a `ScheduleV2` event. No other resources. ECR holds the image.

## Cost Estimate

| 元件 | 費用 |
|------|------|
| Lambda + EventBridge + ECR | 免費額度內 ~$0 |
| Gemini API (`gemini-2.5-flash`) | ~$0.05/月（每天一次呼叫） |
| Finnhub API (free tier) + Telegram Bot | $0 |
| **合計** | **~$0/月（AWS 端不會產生帳單）** |

## Testing

Tests use `pytest-mock` / `MagicMock` for Gemini API and Finnhub HTTP calls. No AWS mocking needed — the code no longer touches AWS services besides what Lambda does at runtime. `tests/conftest.py` only stubs env vars so `config.py` doesn't blow up at import.

## Environment Variables

See `.env.example`. All 4 required, sourced from Lambda env vars in production (set via SAM `--parameter-overrides` at deploy time, NoEcho):

| Var | Purpose |
|-----|---------|
| `FINNHUB_API_KEY` | Finnhub free-tier API key |
| `GEMINI_API_KEY` | Google Gemini API key |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Numeric chat ID of the single recipient |

Local tests stub these in `conftest.py`. For local agent runs (rare), put them in `.env` and source it.

## Coding Guidelines

- State assumptions explicitly before implementing. If multiple interpretations exist, present them.
- Minimum code that solves the problem. No speculative features, abstractions for single-use code, or error handling for impossible scenarios.
- Surgical changes only: don't "improve" adjacent code. Every changed line should trace to the user's request. Remove only orphans YOUR changes created.
- Define verifiable success criteria before starting. For bug fixes, write a reproducing test first.

## Project Status

### Done
- [x] 每日科技股財報推播（scheduler_handler）
- [x] Industry filter via Finnhub `/stock/profile2`
- [x] LLM 中文摘要（Gemini `gemini-2.5-flash`）
- [x] 極簡 SAM stack（單一 Lambda + EventBridge + ECR）

### Phase 2（如果要回頭加功能）
- [ ] Watchlist（需要重新引入持久化儲存）
- [ ] 股價即時查詢 tool（`get_quote`）
- [ ] 財報電話會議逐字稿摘要
