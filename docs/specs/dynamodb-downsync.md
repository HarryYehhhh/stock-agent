# Spec: DynamoDB → 本地 sqlite 每日下行同步（DynamoDB 為 source of truth）

## Goal
確立 DynamoDB 為 Telegram 輸入資料（watchlist、subscribers）的 source of truth，每天午夜把 DynamoDB 完整鏡射回本地 sqlite，並移除 10:30 上行流程中對這些 cloud-written 表的上傳，避免本地 stale 副本回寫覆蓋雲端。

## 背景（已定案，不重開討論）
- Phase 3 已把 webhook 切回雲端 API Gateway：使用者的 `/watch` `/unwatch` `/subscribe` `/unsubscribe` 由雲端 processor 寫入 DynamoDB（watchlist、subscribers）。
- 本地 launchd 每日任務（10:30 台北）讀寫本地 sqlite；雲端寫入不會反映在本地通知裡。
- 雲端 EventBridge 排程維持 DISABLED，每日任務仍由本地跑。
- 現行 10:30 第四步 `make sync-dynamodb`（`upload_local_store --write`）會把本地 watchlist 上傳雲端 → 方向反轉後這會用 stale 本地副本把使用者已 `/unwatch` 的股票重新加回 DynamoDB，必須移除。

## User stories
- As a 使用者, 我在 Telegram（雲端）`/watch` / `/unwatch` / `/subscribe` / `/unsubscribe` 後，隔天本地每日通知就會依照我最新的雲端狀態發送, so that 雲端與本地不再各說各話。
- As a 維運者, 本地 10:30 上行流程不再把 stale 的 watchlist / subscribers 回寫雲端, so that 使用者的刪除操作不會被本地副本復活。

## 決策與範圍（本 spec 定案的開放點）

### 1. 下行同步哪些表
只下行 **watchlist** 與 **subscribers**——這是 Telegram 輸入、雲端寫入、且本地每日 pipeline 會讀的兩張表：
- `watchlist`：`sync-earnings`（`get_active_watchlist_symbols`）、`notify-earnings`（`get_watchlist_earnings_for_date`）會讀。
- `subscribers`：`notify-earnings`（`get_all_active`）、`daily-summary`（推播對象）會讀。

**conversations 不下行**：本地每日 pipeline 完全不讀 conversations，下行無實益，且會把雲端對話歷史複製一份到本地徒增維護面。決定不納入。

`dedup`（processed_updates）、`earnings_calendar`、`company_summaries`、`news`、`market_summaries` 皆不下行（dedup 是雲端 webhook 專用；後四者由本地每日 pipeline 產生，見下方上行）。

### 2. 上行（`upload_local_store`）調整：只搬本地產生的快取
上行改為只上傳 **本地每日 pipeline 產生的快取**：`earnings_calendar`、`company_summaries`、`news`（維持不變）。

移除以下 cloud-written / Telegram 輸入表的上行：
- `watchlist`（**必須**移除——使用者明確要求；否則 10:30 會回寫 stale 覆蓋雲端刪除）。
- `subscribers`（**必須**移除——同屬 Telegram 輸入、雲端 source of truth；避免 stale 訂閱狀態回寫）。
- `conversations`、`dedup`（防禦性移除——同屬 Telegram 輸入、雲端寫入；本地副本可能是 dev/stale 資料，上傳會污染雲端對話歷史或 dedup 表，且對雲端毫無用處。屬「source of truth 原則」的一致延伸，非 scope creep）。

### 3. 下行語意：完整覆蓋（full mirror）
以 DynamoDB 為準的完整覆蓋——雲端刪掉的本地也刪：
1. 先把 DynamoDB `watchlist`、`subscribers` **全量 scan 進記憶體**（正確處理 pagination / `LastEvaluatedKey`）。
2. 全部 scan 成功後，才在**單一 sqlite transaction** 內：`DELETE` 本地 watchlist 全部 rows → `INSERT` scan 到的 watchlist；`DELETE` 本地 subscribers 全部 rows → `INSERT` scan 到的 subscribers；一起 `COMMIT`。
3. **失敗不留半空表**：任一 DynamoDB scan 失敗 → 在動任何本地寫入前中止，本地表原封不動、非 0 exit。transaction 內任一步失敗 → rollback，本地兩表維持覆蓋前狀態，絕不半空。
4. 兩表在同一 transaction 覆蓋以維持 watchlist ⋈ subscribers 的一致性（notify join 依賴）。
5. 型別轉換：DynamoDB `subscribers.active`（Bool）→ 本地 `active`（INTEGER 1/0）；`watchlist.position`（Number）→ INTEGER。缺 `subscribed_at` 時以合理預設（現在時間 iso）回填，不讓本地 NOT NULL 欄位失敗。

### 4. 完整覆蓋 vs. 空表：採嚴格鏡射
若某表 scan 回傳 0 rows，即視為雲端該表確實為空，本地也清空（嚴格鏡射）。理由：
- 契合「完整覆蓋，雲端刪掉本地也刪」語意。
- DynamoDB scan 遇真實錯誤會 raise（而非靜默回空），所以「空」是真的空。
- 若加「回空就不覆蓋」的保護，反而製造正確性破洞（使用者全部 `/unwatch` 或 `/unsubscribe` 後，本地仍留 stale rows → 對已退訂者誤發通知，比清空更糟）。
- 「不留半空表」針對的是**寫入中途失敗**（由 transaction atomicity 解決），與「合法的空狀態」是兩回事。

### 5. seed_watchlist_if_empty 在雙庫下的行為
- 唯一有意義的 seed 路徑是雲端 processor `/subscribe` → `seed_watchlist_if_empty` 寫 DynamoDB（cloud source of truth）。
- 本地每日 pipeline（`sync-earnings` / `notify-earnings` / `daily-summary` / `sync-dynamodb`）**不呼叫** `seed_watchlist_if_empty`；本地 seed 只會在有人對本地 `local_server` 下 `/subscribe`（dev-only）時觸發，而下一次 00:00 下行會把它整表覆蓋掉。
- 結論：無需改動 seed 邏輯；下行完整覆蓋為權威，daily pipeline 不存在會與雲端分岔的本地 re-seed 路徑。此 spec 僅記錄此結論、不改 seed code。

### 6. 排程策略
新增獨立 launchd 排程於 **00:00（台北時間午夜）** 跑下行，與既有 10:30 上行分開：
- 00:00：下行（DynamoDB → 本地 watchlist + subscribers），本地反映隔夜雲端 `/watch` `/unwatch` `/subscribe` `/unsubscribe`。
- 10:30：`sync-earnings`（Finnhub → 本地 earnings_calendar，用剛更新的 watchlist）→ `notify-earnings` → `daily-summary` → `sync-dynamodb`（只上傳本地產生的快取，不再碰 watchlist/subscribers）。
- 兩排程相隔 10.5 小時、無 race；00:00~10:30 之間雲端的改動要到隔日 00:00 才反映（每日粒度，文件說明）。

## Acceptance criteria
- [ ] 新增下行模組（`agent.db.download_remote_store`，`--write` / dry-run 模式對稱於 `upload_local_store`），完整覆蓋本地 watchlist + subscribers。
- [ ] 雲端刪除傳播：DynamoDB watchlist 少了某 symbol，下行後本地該 symbol 也消失。
- [ ] 失敗不留半空表：INSERT 中途失敗 → 本地兩表 rollback 回覆蓋前狀態（非空、非半空）。
- [ ] scan 失敗前置中止：DynamoDB scan raise → 本地表零改動、非 0 exit。
- [ ] pagination 正確：scan 多頁時取回全部 rows。
- [ ] dry-run（無 `--write`）不動本地，印出將覆蓋的 rows 數，exit 0。
- [ ] 冪等：連跑兩次下行，本地狀態相同。
- [ ] 下行不觸碰本地 conversations / dedup / earnings_calendar / company_summaries / news / market_summaries 表。
- [ ] `upload_local_store` 不再上傳 watchlist、subscribers、conversations、dedup；仍上傳 earnings_calendar、company_summaries、news。
- [ ] 新增 `make sync-from-dynamodb` target + `scripts/run-downsync.sh` wrapper + 00:00 launchd plist；文件更新。
- [ ] 全套測試綠（含上行回歸測試、下行完整覆蓋 / atomicity / pagination / dry-run）。

## Non-goals
- 不改 seed_watchlist_if_empty 的邏輯（見決策 5，僅記錄結論）。
- 不下行 conversations（見決策 1）。
- 不啟用雲端 EventBridge、不 `make deploy`（infra 不變——所有需要的 DynamoDB 表已存在）。
- 不改 DynamoDBBackend / LocalSQLiteBackend 既有讀寫方法的語意（下行模組直接對本地 sqlite 表做 DELETE+INSERT，或複用既有 backend；由 generator 選最小實作）。
- 不做即時（real-time）同步；維持每日粒度。
- 不做 conflict resolution（雲端單向覆蓋本地，無雙向 merge）。

## Open questions
- 無阻擋性問題。上述決策 1–6 為本 spec 定案；若 generator 在實作中發現 DynamoDB subscribers 有非預期 shape（例如缺 `subscribed_at`），依決策 3 的預設回填處理並在 QA report 標明。
