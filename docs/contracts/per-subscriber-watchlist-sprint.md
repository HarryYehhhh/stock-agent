# Contract: per-subscriber-watchlist-sprint

對應 spec: docs/specs/per-subscriber-watchlist.md
相關 ADR: docs/decisions/0002-per-subscriber-watchlist-migration.md

術語：「local backend」＝ `src/agent/db/backend.py` 的 `LocalSQLiteBackend`；「watchlist 模組」＝ `src/agent/db/watchlist.py`；「processor」＝ `src/agent/processor_handler.py`；「scheduler」＝ `src/agent/scheduler_handler.py`。測試指令見「Verification commands」。

## Deliverables

### Schema 與欄位改名
- [ ] local backend `_init_schema()` 的 `watchlist` 表改為：`chat_id TEXT NOT NULL, symbol TEXT NOT NULL, exchange TEXT, position INTEGER NOT NULL, PRIMARY KEY (chat_id, symbol)`，並建立 `CREATE INDEX IF NOT EXISTS idx_watchlist_symbol ON watchlist(symbol)`。
- [ ] 將 watchlist 相關程式碼的 `ticker` 命名全部改為 `symbol`：`DEFAULT_WATCHLIST` 的 dict key（`{"exchange": ..., "symbol": ...}`）、backend watchlist 方法的欄位與回傳 key、scheduler 對 watchlist item 的存取、以及任何相關 tools/handlers。

### Migration
- [ ] `_init_schema()` 偵測舊 shape（`watchlist` 表存在且無 `chat_id` 欄位）並一次性遷移：讀舊全域 rows（`ticker, exchange, position`）；對 `subscribers` 表每個 `chat_id` 產生等同的個人 rows（`symbol = ticker`，保留 `exchange`、`position`）；無 subscriber 時舊 rows 丟棄。
- [ ] Migration idempotent：新 shape 下不重複執行、不覆寫既有資料。

### Backend 方法（local）
- [ ] `get_watchlist(chat_id)`：回傳該使用者清單，依 `position` 排序，每筆至少含 `symbol`、`exchange`。
- [ ] 新增一檔：把 symbol（大寫正規化）加入該 chat_id 的 watchlist，`position` 取該使用者現有最大值 +1；已存在時為 no-op（回傳是否實際新增）。
- [ ] 移除一檔：刪除該 chat_id 的指定 symbol（回傳是否實際刪除）。
- [ ] seed-if-empty：僅在該 chat_id 尚無任何 watchlist row 時，以 `DEFAULT_WATCHLIST` 寫入（帶 position 與 exchange）。
- [ ] per-subscriber 通知 join：提供方法回傳「所有 active subscriber 在指定 `earning_date` 有財報的 watchlist 項目」，每筆含 `chat_id, symbol, exchange, earning_date` 及必要的 `extended_hour / eps_estimate / revenue_estimate / updated_at`；來源為 `earnings_calendar ⋈ watchlist ⋈ subscribers WHERE active = 1`。
- [ ] 提供方法回傳「所有 active subscriber watchlist 的 distinct symbol（含 exchange）」，供 scheduler 同步階段抓財報用。
- [ ] 移除 `_seed_watchlist()` 全域覆蓋邏輯及其在 `_init_schema()` 的呼叫。

### Backend 方法（dynamodb 相容）
- [ ] watchlist 模組 `get_watchlist(chat_id)` 在 dynamodb backend 回傳全域 `DEFAULT_WATCHLIST`（維持 `_notify_live()` 現狀，可忽略 chat_id）。
- [ ] watchlist add / remove 對 dynamodb backend raise `NotImplementedError`。

### Scheduler
- [ ] local 路徑同步階段改抓「所有 active subscriber watchlist 的 distinct symbol」的未來財報（取代迭代全域 watchlist）。
- [ ] local 路徑通知階段改用 per-subscriber join：逐 subscriber 取其隔天有財報的 symbol → `unnotified_symbols` 過濾 → 針對該 subscriber symbols 產生 summary → `send_message` → `mark_notified`。
- [ ] 沿用 Finnhub 失敗時的 stale banner + 快取降級行為。
- [ ] dynamodb 路徑 `_notify_live()` 行為與輸出不變。

### Processor 指令
- [ ] `/watchlist`：回傳呼叫者自己的 watchlist；空清單回覆提示與新增用法。
- [ ] `/watch <SYMBOL>`：新增一檔（大寫正規化）；缺參數或格式無效 → 用法說明；重複新增 → 明確非錯誤語氣回覆。
- [ ] `/unwatch <SYMBOL>`：移除一檔；缺參數 → 用法說明；移除不存在項 → 明確非錯誤語氣回覆。
- [ ] dynamodb backend 下的 add/remove 指令攔截 `NotImplementedError` 並回覆「目前僅本地模式支援」之類訊息，不讓 handler crash。
- [ ] `/subscribe` 在既有 subscribe 動作後，對尚無 watchlist 的使用者呼叫 seed-if-empty。
- [ ] 更新 `/start`、`/help` 的 WELCOME 文案，加入 `/watchlist`、`/watch`、`/unwatch` 說明。

## Out of scope
- Per-user watchlist 的 DynamoDB 表 / GSI 實作。
- watchlist REST endpoint、批次多檔指令、`/watch` 時查 exchange。
- 更動 conversations / dedup / news / market_summaries / earnings_notifications schema。
- 把 watchlist 納入 upload-to-DynamoDB command。

## Tests required
- [ ] Schema：新建 local store 的 `watchlist` 表為 junction shape（PK `(chat_id, symbol)`，欄位含 `chat_id/symbol/exchange/position`），且 `idx_watchlist_symbol` 存在。
- [ ] Migration：以舊 shape（`ticker PRIMARY KEY, exchange, position`）預先建表並塞全域 rows + 幾個 subscriber，建構 `LocalSQLiteBackend` 後，每個 subscriber 都得到等同舊清單的個人 watchlist；重複建構不重複/不覆寫。
- [ ] Seed：`/subscribe` 對新 chat_id 以 `DEFAULT_WATCHLIST` seed；對已有自訂 watchlist 的 chat_id 不覆蓋；backend 重新建構後自訂項目仍在（驗證 `_seed_watchlist` 覆蓋已移除）。
- [ ] 指令：`/watch` 新增後出現在 `/watchlist`；`/unwatch` 後消失；symbol 大寫正規化；無效參數回用法說明。
- [ ] 隔離：兩個 chat_id 各自的 `/watchlist` 互不可見。
- [ ] Scheduler per-user：兩個 subscriber 追蹤不同 symbol、隔天各有財報時，各自只收到自己 symbol 的推播；重跑不重複（沿用 `earnings_notifications`）。
- [ ] Scheduler 降級：Finnhub 同步失敗但有快取時仍推播且帶 stale banner（沿用現有行為，於 per-user 模式下驗證）。
- [ ] dynamodb 相容：`_notify_live()` 行為不變（既有 `tests/test_scheduler.py` dynamodb 案例調整為 `symbol` 命名後仍通過）；dynamodb 下 add/remove 指令回覆「僅本地模式支援」而非拋例外。
- [ ] 更新既有受影響測試：`tests/test_watchlist.py`、`tests/test_scheduler.py` 對齊新 schema / 新命名 / 新 seed 行為。

## Verification commands
- 全套測試：`make test`（等同 `PYTHONPATH=src uv run pytest tests/ -v`）
- 單一測試：`PYTHONPATH=src uv run pytest tests/test_watchlist.py -v -k test_name`
- 手動排程（local）：`make notify-earnings`（若需人工驗證，非自動化 gate）

## Definition of done
- 全部 deliverable 打勾。
- `make test` 全綠，且不呼叫真 AWS / Groq / Finnhub / Telegram。
- 既有 local DB migration 於測試中驗證通過（既有 subscriber 不掉清單）。
- evaluator 的 QA report 無 P0/P1。
