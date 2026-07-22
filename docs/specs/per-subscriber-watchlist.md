# Spec: Per-subscriber custom watchlist

## Goal
把目前的「全域固定 watchlist」改成「每個訂閱者可自訂的 watchlist」，並把資料層 `ticker` 欄位/命名對齊 `earnings_calendar.symbol`，讓每日財報通知只推播該訂閱者自己清單中隔天有財報的股票。

## User stories
- As a subscriber, I want to see my own watchlist, so that I know which tickers the bot is tracking for me.
- As a subscriber, I want to add a symbol to my watchlist, so that I get earnings reminders for stocks I care about.
- As a subscriber, I want to remove a symbol from my watchlist, so that I stop getting reminders I no longer want.
- As a subscriber, I want a sensible default watchlist when I first subscribe, so that the bot is useful immediately without manual setup.
- As a maintainer, I want the notification join to be driven by SQL (earnings_calendar ⋈ watchlist ⋈ subscribers), so that each subscriber only gets reminders for their own symbols with per-user idempotency.
- As a maintainer, I want existing local DBs to migrate cleanly, so that current subscribers do not silently lose their watchlist or notifications.

## Background（現況）
- Local backend（`src/agent/db/backend.py`）目前的 `watchlist` 表是全域單一清單：`ticker TEXT PRIMARY KEY, exchange TEXT NOT NULL, position INTEGER NOT NULL`。
- `_init_schema()` 每次啟動都呼叫 `_seed_watchlist()`，用 `DEFAULT_WATCHLIST`（27 檔）**強制覆蓋**整張表——`INSERT ... ON CONFLICT UPDATE` 加上 `DELETE ... WHERE ticker NOT IN (...)`。這會在每次 backend 建構時洗掉任何自訂項目。
- `scheduler_handler.py` local 路徑：`_sync_earnings_calendar()` 逐檔抓未來 30 天財報寫入 sqlite，再用 `get_watchlist_earnings_for_date(tomorrow)`（watchlist ⋈ earnings_calendar 全域 join）取出隔天有財報者，對「每個 active subscriber」用 `unnotified_symbols` / `mark_notified` 做 per-user 去重，最後**共用同一份 summary** 推給所有 pending subscriber。
- `earnings_notifications` 表 PK 已是 `(chat_id, symbol, earning_date)`，本來就支援 per-user 去重，沿用。
- DynamoDB（Lambda production）路徑 `_notify_live()` 仍走 live-fetch + 全域 `DEFAULT_WATCHLIST`，所有 subscriber 收到同一份。
- `ticker` 命名散落於：`DEFAULT_WATCHLIST` 的 dict key、`watchlist.get_watchlist()` 回傳 shape、`backend.py` 的 watchlist 相關方法、`scheduler_handler.py` 的 `item["ticker"]`、`tests/test_watchlist.py` 與 `tests/test_scheduler.py`。

## Scope of change

### 1. Schema：watchlist 改 junction table + 欄位改名
- 新 schema：
  ```sql
  CREATE TABLE watchlist (
      chat_id  TEXT NOT NULL,
      symbol   TEXT NOT NULL,
      exchange TEXT,
      position INTEGER NOT NULL,
      PRIMARY KEY (chat_id, symbol)
  );
  CREATE INDEX idx_watchlist_symbol ON watchlist(symbol);
  ```
- `ticker` 一律改名為 `symbol`：包含 `DEFAULT_WATCHLIST` 的 dict key、backend 方法的欄位/回傳 key、`scheduler_handler` 的存取、以及相關 tools/handlers 命名。對齊 `earnings_calendar.symbol`。
- `exchange` 改為 nullable：`DEFAULT_WATCHLIST` seed 帶 exchange；使用者以指令新增的 symbol 在未知交易所時存 `NULL`。

### 2. Migration（既有 local DB）
- 偵測舊 schema（`watchlist` 表存在且沒有 `chat_id` 欄位），一次性遷移：
  - 讀出舊全域 rows（`ticker, exchange, position`）。
  - 對 `subscribers` 表中每個 `chat_id`，把舊全域清單複製成該 subscriber 的個人清單（`symbol = ticker`，保留 `exchange`、`position`）。
  - 若當下沒有任何 subscriber，舊全域 rows 直接丟棄（新表為空），之後 `/subscribe` 會以 `DEFAULT_WATCHLIST` seed。
- Migration 必須 idempotent：只有偵測到舊 shape 時才執行；新 shape 直接略過。
- 決策細節與替代方案見 ADR-0002。

### 3. 每日通知（scheduler local 路徑）
- 同步階段：抓「所有 active subscriber watchlist 的 symbol 聯集」的未來財報寫入 sqlite（取代原本迭代全域 watchlist）。
- 通知階段：用 SQL join `earnings_calendar ⋈ watchlist ⋈ subscribers WHERE active = 1 AND earning_date = tomorrow`，得到每個 subscriber 隔天有財報的 symbol。
- 逐 subscriber：以 `unnotified_symbols` 過濾已通知者 → 針對該 subscriber 的 pending symbols 產生 summary → `send_message` → `mark_notified`。
- summary 為 per-subscriber（各自清單不同）。小規模訂閱者可接受多次 `run_agent`；trade-off 見下方 Open questions。
- Finnhub 同步失敗時沿用現有「stale banner + 快取資料」降級行為。

### 4. Telegram 指令（processor_handler routing）
- `/watchlist`：列出自己的 watchlist（symbol，若有 exchange 一併顯示；空清單給提示與新增用法）。
- `/watch <SYMBOL>`：新增一檔到自己的 watchlist（symbol 正規化為大寫；exchange 未知存 NULL）。
- `/unwatch <SYMBOL>`：從自己的 watchlist 移除一檔。
- 無效/缺參數 → 回覆用法說明；重複新增 / 移除不存在項 → 給明確、非錯誤語氣的回覆。
- 更新 `/start`、`/help` 的 WELCOME 文字，列出上述指令。

### 5. Seed 行為
- 移除 `_seed_watchlist()` 全域強制同步（連同每次啟動的覆蓋邏輯）。
- `/subscribe` 時，**僅在該 chat_id 尚無任何 watchlist row 時**，以 `DEFAULT_WATCHLIST` seed 該使用者。

### 6. DynamoDB backend（本 sprint 最小處理）
- 維持 local-first：per-user watchlist 的完整實作只在 local backend。
- DynamoDB 路徑：
  - `get_watchlist(chat_id)` 仍回傳全域 `DEFAULT_WATCHLIST`（維持 `_notify_live()` 現狀）。
  - watchlist 管理（add/remove）在 dynamodb backend 尚未支援；對應方法 raise `NotImplementedError`，processor 指令 handler 攔截並回覆「目前僅本地模式支援」之類訊息（不讓 Lambda crash）。
- 未來 DynamoDB 設計方向（本 sprint 不實作，僅記錄）：
  - 新增 per-user watchlist 表，`PK = chat_id (S)`、`SK = symbol (S)`，item 帶 `exchange`、`position`。
  - 反向查詢（哪些 subscriber 追蹤某 symbol）用 GSI，`GSI PK = symbol`。
  - 通知流程改為 query 每個 active subscriber 的 watchlist，或以 symbol GSI 反查，取代目前全域 live-fetch。

## Acceptance criteria
- [ ] Local `watchlist` 表為 junction 形態：PK `(chat_id, symbol)`，欄位 `chat_id, symbol, exchange(nullable), position`，且存在 `idx_watchlist_symbol`。
- [ ] 程式碼層不再有 `ticker` 命名於 watchlist 相關路徑（`DEFAULT_WATCHLIST` key、backend 方法、scheduler、tools/handlers）；一律 `symbol`。
- [ ] 既有舊 shape local DB 啟動時自動遷移：每個既有 subscriber 得到等同舊全域清單的個人 watchlist；migration 可重複執行不重覆寫。
- [ ] 移除 `_seed_watchlist()` 全域覆蓋；自訂項目在 backend 重新建構後不再被洗掉。
- [ ] `/subscribe` 對尚無 watchlist 的使用者以 `DEFAULT_WATCHLIST` seed；對已有 watchlist（含既有自訂）的使用者不覆蓋。
- [ ] `/watchlist` 回傳呼叫者自己的清單；不同 chat_id 互不可見。
- [ ] `/watch <SYM>` 新增後，該 symbol 出現在該使用者 `/watchlist`，且 symbol 已大寫正規化。
- [ ] `/unwatch <SYM>` 移除後，該 symbol 不再出現於該使用者 `/watchlist`。
- [ ] 無效指令參數回覆用法說明，不丟例外。
- [ ] Scheduler local 路徑只推播「該 subscriber watchlist 中隔天有財報」的 symbol，且跨 subscriber 使用 SQL join（earnings_calendar ⋈ watchlist ⋈ subscribers WHERE active=1）。
- [ ] 兩個 subscriber 追蹤不同 symbol 時，各自只收到自己 symbol 的提醒；per-user 去重沿用 `earnings_notifications`，重跑不重複推播。
- [ ] DynamoDB 路徑 `_notify_live()` 行為不變（全域 `DEFAULT_WATCHLIST`）；dynamodb 下呼叫 watchlist add/remove raise `NotImplementedError` 並由 processor 優雅回覆。
- [ ] `make test` 全綠；不呼叫真 AWS / Groq / Finnhub / Telegram。

## Non-goals
- 不實作 per-user watchlist 的 DynamoDB 儲存或 GSI（僅記錄設計方向）。
- 不做 watchlist 的 REST endpoint（`local_server.py` 不新增 watchlist route，除非測試需要）。
- 不支援單一指令批次新增/移除多檔（一次一檔）。
- 不在 `/watch` 時呼叫 Finnhub 補 exchange（未知即存 NULL）。
- 不改 conversations / dedup / news / market_summaries / earnings_notifications schema。
- 不把 watchlist 納入 upload-to-DynamoDB command。
- 不改 Telegram webhook / SQS / agent tool-use loop。

## Open questions
- Per-subscriber summary 會讓 `run_agent` 呼叫次數隨 pending subscriber 數線性增加。本 sprint 假設訂閱者數量小、可接受；若未來規模變大，可依「相同 symbol 集合」快取 summary 或合併推播。（決定：本 sprint 走 per-subscriber，最簡單正確。）
- `/watch` 新增時不查 exchange（存 NULL），通知 prompt 需容忍缺 exchange 的 symbol。若日後要求交易所標註完整，再補 profile2 查詢。
- 使用者把清單清空後再 `/subscribe` 會重新 seed DEFAULT_WATCHLIST（因為「尚無 watchlist」）。本 sprint 視為可接受邊界行為。
