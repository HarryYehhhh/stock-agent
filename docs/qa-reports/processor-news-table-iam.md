# QA Report: processor-news-table-iam

- **需求**: 修正 Processor Lambda 缺 NewsTable IAM 權限（`infra/template.yaml` ProcessorFunction 加 `DynamoDBCrudPolicy: TableName: !Ref NewsTable`），並盤點 Processor 其他工具/路徑是否還有同類缺權限
- **Date**: 2026-07-31
- **Verdict**: ✅ Pass

## 需求檢查清單

| 項目 | Status | Note |
|---|---|---|
| ProcessorFunction 加 NewsTable DynamoDBCrudPolicy | ✅ | `infra/template.yaml:361-362`，位置在 DynamoDB policies 群組內（`CorrectionsTable` 之後、`SQSPollerPolicy` 之前），縮排/寫法與同區塊其餘 6 條 `DynamoDBCrudPolicy` 條目一致 |
| 盤點 Processor 其他工具/路徑是否還有同類缺權限 | ✅ | 獨立重做盤點（見下），確認除 NewsTable 外無其他缺漏 |
| 改動範圍最小 | ✅ | `git diff` 只有 `infra/template.yaml` +2 行，未動 Python code |

## 獨立重做盤點

沒有直接信任 generator 的表，自己從 `db/backend.py`（唯一直接碰 DynamoDB 的模組）逆向追蹤每個 `StorageBackend` 方法被誰呼叫、再對照三個 Lambda 各自的 Policies：

**ProcessorFunction 實際會觸的表（逐一 grep 呼叫鏈確認）：**

| Backend 方法 | 呼叫路徑 | 對應表 | Policy 現況 |
|---|---|---|---|
| `save_message` / `get_history` | `processor_handler.py` → `db/conversations.py` | ConversationsTable | ✅ 已有 |
| `subscribe` / `unsubscribe` | `processor_handler.py` → `db/subscribers.py` | SubscribersTable | ✅ 已有 |
| `get_watchlist` / `add_to_watchlist` / `remove_from_watchlist` / `seed_watchlist_if_empty` | `processor_handler.py` → `db/watchlist.py` | WatchlistTable | ✅ 已有 |
| `save_correction` | `processor_handler.py` → `db/corrections.py` | CorrectionsTable | ✅ 已有 |
| `save_trace` | `agent.py:211`（`run_agent` 每輪結束） | TracesTable | ✅ 已有 |
| `upsert_earnings_calendar` | `tools/earnings.py:103`（`get_earnings_calendar` 工具寫快取） | EarningsCalendarTable | ✅ 已有 |
| `list_news_since` / `insert_news` / `insert_market_summary` / `get_cached_market_summary` | `tools/market_summary_tool.py` → `market_summary.py`（`get_market_summary` 工具，`generate_market_summary`） | **NewsTable**（含 market summary 快取，`bucket="market_summary"` 共表不共表名，見 `db/backend.py:1503-1530` docstring） | ✅ **本次修正加上** |

**確認 Processor 不需要、且本次未被誤動的表：**
- `ProcessedUpdatesTable` / `RateLimitsTable` — grep 確認 `claim_update_id` 只被 `webhook_handler.py:65` 呼叫、`check_rate_limit`（內部呼叫 `increment_rate_limit`/`mark_rate_limit_notified`）只被 `webhook_handler.py:72` 呼叫，Processor 完全不碰。WebhookFunction policies（`template.yaml:314-317`）維持原樣未變動。
- `EarningsNotificationsTable` / `CompanySummariesTable` — grep 確認 `mark_notified`/`unnotified_symbols`/`get_company_summary`/`upsert_company_summary` 只被 `scheduler_handler.py` 呼叫；`summary_notified`/`mark_summary_notified` 只被 `market_summary.py:408,415` 的 `push_daily_summary()` 呼叫，而 `push_daily_summary` 只被 `scheduler_handler.py:207` 呼叫（Processor 走的是 `get_market_summary` 工具 → `generate_market_summary`，不經過 `push_daily_summary`）。SchedulerFunction policies（`template.yaml:394-408`）維持原樣未變動。
- `tools/quote.py`、`tools/news.py`（純 helper，被 `market_summary.py` import 使用，不直接碰 backend）、`tools/knowledge_base_tool.py`（純本地檔案 `knowledge/*.md`，不碰 DB）、`fast_path.py`、`knowledge/retrieval.py` — grep `get_backend`/`resource.Table` 全部無匹配，確認無 DB 依賴。

盤點結論與 generator 摘要一致：ProcessorFunction 需要且僅需要 Conversations / Subscribers / Watchlist / EarningsCalendar / Traces / Corrections / News 七張表，修正前缺 NewsTable 一張，本次已補齊，無其他遺漏。

## Test results

- 測試指令 1：`cd infra && sam validate --lint`
  結果：`infra/template.yaml is a valid SAM Template`
- 測試指令 2：`PYTHONPATH=src uv run pytest tests/ -q --ignore=.env --ignore=.env.example`
  結果：541 passed / 13 errors（全部為 `tests/test_local_integration.py` 既有的 sandbox socket bind 限制 `PermissionError: [Errno 1] Operation not permitted`，與本次改動無關——本次只動 `infra/template.yaml`，不觸及任何 Python code / import 路徑，`test_local_integration.py` 本身也不 import 到本次改動範圍）
  既有 suite regression：✅ 無（passed 數與 generator 回報的 541 passed / 13 errors 完全一致）

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）
無

### 🟢 Suggestions
無

### ✅ What looks good
- `template.yaml` diff 精準對齊需求，位置放在既有 DynamoDB policies 群組尾端（`CorrectionsTable` 之後），與其餘 6 條同 pattern（`DynamoDBCrudPolicy` + 兩行縮排）完全一致，未動到 Webhook / Scheduler 的 Policies 區塊
- 盤點方法紮實：從 `db/backend.py` 這個唯一的 DynamoDB 存取點反向追每個方法的呼叫鏈，而非只列工具清單，能抓到「market summary 快取借用 NewsTable」這種非顯而易見的共表情況（`insert_market_summary`/`get_cached_market_summary` 也寫在 NewsTable，用 `bucket="market_summary"` 區分，backend.py 內有明確 docstring 說明原因）
- 用 `Crud`（非唯讀 policy）是對的：Processor 同時需要 `list_news_since`（讀）與 `insert_news`/`insert_market_summary`（寫，市場摘要快取回填）

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無（本次改動範圍小且已驗證完整，無待辦）
