# Contract: DynamoDB production tables + DynamoDBBackend 補實作（Phase 2 PR-1）

對應 spec: docs/specs/dynamodb-production-tables.md
對應 ADR: docs/decisions/0004-dynamodb-production-tables.md
背景: docs/TASKS.md Phase 2 PR-1

> 專案中立說明：以下「應用程式碼路徑」「測試路徑」「測試指令」請對照 `CLAUDE.md` 解讀為具體值（測試指令＝`make test` ＝ `PYTHONPATH=src uv run pytest tests/ -v`）。

## Deliverables

### D1. config.py — 四個 table name accessor
- [ ] 在設定模組（`src/agent/config.py`）新增 `watchlist_table()` / `earnings_calendar_table()` / `earnings_notifications_table()` / `company_summaries_table()`，各讀對應 env（`WATCHLIST_TABLE` / `EARNINGS_CALENDAR_TABLE` / `EARNINGS_NOTIFICATIONS_TABLE` / `COMPANY_SUMMARIES_TABLE`），fallback 分別為 `earnings-agent-watchlist` / `-earnings-calendar` / `-earnings-notifications` / `-company-summaries`。風格比照既有 `conversations_table()`。

### D2. StorageBackend Protocol — 加入升格方法
- [ ] 在 `StorageBackend` Protocol（`src/agent/db/backend.py`）新增 7 個方法簽章：`get_active_watchlist_symbols`、`get_watchlist_earnings_for_date`、`get_chat_watchlist_earnings`、`unnotified_symbols`、`mark_notified`、`get_company_summary`、`upsert_company_summary`。
- [ ] 更新 Protocol docstring：說明這些方法已升格為跨 backend 共用（延伸 ADR-0003），news / market_summaries / export_rows 仍 local-only。
- [ ] `LocalSQLiteBackend` 已實作這些方法，簽章需與 Protocol 一致（不改 local 行為）。

### D3. DynamoDBBackend — 補實作（行為對齊 LocalSQLiteBackend）
在 `DynamoDBBackend`（`src/agent/db/backend.py`）：
- [ ] `__init__` 建立四張新表的 `Table` resource（比照既有三張表）。
- [ ] `get_watchlist(chat_id=None)`：`chat_id` 非 None → query PK=chat_id、Python 端依 `position` `sorted()`、回 `[{"symbol": ...}]`；`chat_id is None` → 回 `DEFAULT_WATCHLIST` copy（過渡分支，註解標明 PR-2 移除）。
- [ ] `add_to_watchlist(chat_id, symbol)`：`symbol.upper()`；query 現有項算 `next_position`（空清單為 0）；conditional put `attribute_not_exists(symbol)`；已存在回 `False`、新增回 `True`。
- [ ] `remove_from_watchlist(chat_id, symbol)`：`symbol.upper()`；`delete_item` + `ConditionExpression=attribute_exists(symbol)`；成功 `True`、`ConditionalCheckFailedException` 回 `False`。
- [ ] `seed_watchlist_if_empty(chat_id)`：query `Limit=1` 判空；空則 `batch_writer` 寫入 `DEFAULT_WATCHLIST`（`enumerate` position）。
- [ ] `upsert_earnings_calendar(items)`：跳過缺 `symbol`/`date` 的 item；`put_item`（`symbol.upper()`、`earning_date=item["date"]`、`extended_hour=item.get("hour")`、`eps_estimate`/`revenue_estimate` → `Decimal(str(v))`、`None` 不寫該屬性、`updated_at=now_iso`、`ttl=now+45天`）；空 items return。
- [ ] `get_active_watchlist_symbols()`：`get_all_active()` → 逐 chat_id query watchlist → set 聯集 → 回 `[{"symbol": ...}]` 依 symbol 排序。
- [ ] `get_watchlist_earnings_for_date(earning_date)`：GSI `earning-date-index` query 當日 earnings（處理分頁）→ 逐 active subscriber query watchlist → 交集產列 `{chat_id, symbol, earning_date, extended_hour, eps_estimate, revenue_estimate, updated_at}`，數值欄位讀回 `float`，輸出依 `chat_id`、`position` 排序。
- [ ] `get_chat_watchlist_earnings(chat_id, date_from, date_to)`：query watchlist → 逐 symbol query earnings_calendar `SK BETWEEN`（或 BatchGetItem）→ 回列（數值 `float`），依 `earning_date`、`position` 排序。
- [ ] `unnotified_symbols(chat_id, symbols, earning_date)`：空 `symbols` 回 `[]`；檢查各 `notify_key=f"{symbol}#{earning_date}"` 是否存在；回未通知的 symbols（保持傳入順序）。
- [ ] `mark_notified(chat_id, symbols, earning_date)`：逐 symbol conditional put `attribute_not_exists(notify_key)`（含 `symbol`/`earning_date`/`sent_at=now`/`ttl=now+30天`）；吞 `ConditionalCheckFailedException`。
- [ ] `get_company_summary(symbol, earning_date)`：`get_item` → 回 `summary` 或 `None`。
- [ ] `upsert_company_summary(symbol, earning_date, summary)`：`put_item`（`created_at=now`、`ttl=now+30天`）。
- [ ] 新增 TTL 常數（比照既有 `CONVERSATION_TTL_SECONDS` 風格）：earnings_calendar 45 天、notifications 30 天、company_summaries 30 天。

### D4. infra/template.yaml — 四張表 + env + IAM
- [ ] 新增 `WatchlistTable`（PK chat_id S / SK symbol S、無 TTL、無 GSI）。
- [ ] 新增 `EarningsCalendarTable`（PK symbol S / SK earning_date S、GSI `earning-date-index` PK earning_date S Projection ALL、TTL on `ttl`）。
- [ ] 新增 `EarningsNotificationsTable`（PK chat_id S / SK notify_key S、TTL on `ttl`）。
- [ ] 新增 `CompanySummariesTable`（PK symbol S / SK earning_date S、TTL on `ttl`）。全部 `BillingMode: PAY_PER_REQUEST`。
- [ ] `Globals.Function.Environment.Variables` 加入四個 table name env（`!Ref` 對應表）。
- [ ] ProcessorFunction：新增 `DynamoDBCrudPolicy` on `WatchlistTable`。
- [ ] SchedulerFunction：新增 `DynamoDBCrudPolicy` on `WatchlistTable` / `EarningsCalendarTable` / `EarningsNotificationsTable` / `CompanySummariesTable`（保留既有 subscribers 讀取權）。

### D5. db/upload_local_store.py — 納入 watchlist（不含快取表）
- [ ] `LocalSQLiteBackend.export_rows()` 回傳新增 `"watchlist"` 清單（`chat_id, symbol, position`，依 `chat_id, position` 排序）。
- [ ] `_write_rows` 寫入 watchlist 表（`put_item`）。
- [ ] dry-run/write 計數行加入 `watchlist=<n>`。
- [ ] 不加入 earnings_calendar / earnings_notifications / company_summaries。

## 行為與邊界
- 當 `chat_id` 的 watchlist 為空，`get_watchlist(chat_id)` 回 `[]`（非 DEFAULT）。
- 當 `add_to_watchlist` 重複加同一 symbol，回 `False`、不改 position。
- 當 `remove_from_watchlist` 移除不存在項，回 `False`、不報錯。
- 當 `mark_notified` 對已標記 `(chat_id, symbol, earning_date)` 重跑 → 不報錯；後續 `unnotified_symbols` 不再回該 symbol。
- 當 earnings item 的 `eps_estimate`/`revenue_estimate` 為 `None` → 不寫該屬性；讀回時該 key 不存在或為 `None`，與 local 一致。
- 錯誤處理：只處理 `ConditionalCheckFailedException`（add/remove/mark_notified）；其餘 `ClientError` 往上拋（同既有 `claim_update_id` 模式），不吞。

## Out of scope（本 sprint 不做）
- `scheduler_handler.py` 任何邏輯改動（含 `_notify_live` 移除、sync/notify 走 DynamoDB）→ PR-2。
- `processor_handler.py` 任何改動（含 `LOCAL_ONLY_MESSAGE` 死碼移除）→ PR-2。
- news / market_summaries 的 DynamoDB 方法與表 → PR-3。
- watchlist symbol GSI。
- 真實 AWS 部署 → Phase 3。

## Tests required
（moto mock，慣例見 `tests/conftest.py`、CLAUDE.md Testing 段；不打真 AWS/Groq/Finnhub/Telegram）
- [ ] **conftest**：`aws` fixture 建出四張新表（earnings_calendar 含 `earning-date-index` GSI），並在 `clear_db_caches` / env fixture 設定四個 table name env。
- [ ] **watchlist（dynamodb）**：`get_watchlist(chat_id)` 回自己的清單且依 position 排序；`add_to_watchlist` 新增/重複的 True/False 與大寫正規化；`remove_from_watchlist` 存在/不存在的 True/False；`seed_watchlist_if_empty` 空清單 seed DEFAULT、非空不覆蓋；`get_watchlist(None)` 仍回 DEFAULT_WATCHLIST。
- [ ] **既有測試更新**：`tests/test_watchlist.py::test_add_remove_raise_not_implemented_for_dynamodb` 改為驗證 dynamodb 下 add/remove 真實運作（不再 `pytest.raises(NotImplementedError)`）。
- [ ] **earnings 快取 + join（dynamodb）**：`upsert_earnings_calendar` 寫入後 `get_active_watchlist_symbols` / `get_watchlist_earnings_for_date` / `get_chat_watchlist_earnings` 回傳與 local 對齊（含數值讀回為 float、per-subscriber scoping、兩 subscriber 追蹤不同 symbol 各自只拿到自己列、日期區間過濾）。
- [ ] **冪等（dynamodb）**：`unnotified_symbols` → `mark_notified` → 再 `unnotified_symbols` 該 symbol 不再出現；`mark_notified` 重跑不拋例外。
- [ ] **company summary（dynamodb）**：`upsert_company_summary` 後 `get_company_summary` 回該摘要；不存在回 `None`。
- [ ] **upload_local_store**：dry-run 計數含 watchlist；`--write` 後 watchlist rows 進 DynamoDB 表、三個快取表未被寫入（moto 驗證）。
- [ ] **對齊測試（建議）**：同一組輸入分別跑 local 與 dynamodb backend，斷言公開回傳等價（symbol 集合、排序、去重結果），鎖住行為 parity。

## Definition of done
- 全部 deliverable 打勾。
- `make test`（`PYTHONPATH=src uv run pytest tests/ -v`）全綠。
- `scheduler_handler.py` 與 `processor_handler.py` 原始碼 diff 為零。
- evaluator 的 QA report 無 P0/P1。
