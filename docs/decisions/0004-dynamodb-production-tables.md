# ADR-0004: DynamoDB production 表設計與 backend 補實作（Phase 2 PR-1）

- **Date**: 2026-07-24
- **Status**: Accepted
- **關聯**: 延伸 ADR-0003（StorageBackend Protocol 範圍）；實作 `docs/specs/dynamodb-production-tables.md`

## Context
Phase 1 已在本地 sqlite 跑通 DB-first watchlist 財報通知流程（per-subscriber、冪等、摘要快取）。要遷到 production，DynamoDB 這半邊仍是 no-op / `NotImplementedError` / degenerate（`get_watchlist` 回全域 `DEFAULT_WATCHLIST`），且對應的表尚未在 SAM 定義。PR-1 需一次決定：表怎麼切、join 怎麼做、冪等怎麼保證、Protocol 範圍要不要擴、本地資料要不要搬上雲。這些多為不可逆或牽動介面的決策，故立 ADR。

方向已於 2026-07-23/24 與使用者定案，本 ADR 記錄決定與理由，非重開討論。

## Decision

### 1. 多表風格，不做 single-table design
沿用既有 conversations / subscribers / processed-updates 三表的一表一用途風格，新增四張表：`watchlist`、`earnings_calendar`、`earnings_notifications`、`company_summaries`，各自 PK/SK 直接對映 local sqlite 表。全部 `PAY_PER_REQUEST`（on-demand），與既有三表一致、免容量規劃、切合單一使用者/低流量。

### 2. Application-side join，不靠 DynamoDB 反查
三個 local SQL join 一律在 Python 端重現：
1. sync distinct symbols：`get_all_active()` → 逐 chat_id query watchlist → set 聯集。
2. notify by date：`earning-date-index` GSI 查當日 earnings 一次 → 逐 active subscriber query watchlist → 記憶體交集。
3. 單一 chat 日期區間：query watchlist → 逐 symbol query earnings_calendar `SK BETWEEN`（或 BatchGetItem）。

### 3. watchlist 只設 symbol GSI 之「不設」
watchlist 不建 `symbol` 反查 GSI（spec 草案 per-subscriber-watchlist.md:65-73 曾提）。上述 join 從不需要「由 symbol 反查 subscriber」——notify 流程改以 GSI 查 earnings（而非查 watchlist）再與每個 subscriber 的 watchlist 交集。省一個 GSI 降低寫入成本與複雜度。

### 4. 通知冪等改用 conditional put
`earnings_notifications` 以複合 SK `symbol#earning_date`、`mark_notified` 用 `attribute_not_exists(notify_key)` conditional put、吞 `ConditionalCheckFailedException`，對齊現有 `claim_update_id` pattern，語意等同 local 的 `INSERT OR IGNORE`。維持 `unnotified_symbols`（讀）+ `mark_notified`（寫）兩段式，與 local/scheduler 現有呼叫形狀一致，不合併成單一 claim。

### 5. TTL 策略
- watchlist：**無 TTL**（使用者資料，永久）。
- earnings_calendar：`ttl = now + 45 天`，每次 upsert 刷新；仍相關的未來財報持續被覆寫延命，過期財報約 45 天後清除（30 天 sync horizon + 15 天緩衝）。
- earnings_notifications：`ttl = now + 30 天`（財報日過後去重標記即無用）。
- company_summaries：`ttl = now + 30 天`（摘要綁定該次財報日）。

### 6. 數值型別
`eps_estimate` / `revenue_estimate` 寫入轉 `Decimal(str(v))`（boto3 resource 不吃 float），讀取轉回 `float`，使 DynamoDB 回傳 shape 與 `LocalSQLiteBackend`（REAL/float）一致；`None` 不寫該屬性以保 nullable。

### 7. Protocol 範圍延伸（延伸 ADR-0003）
本 PR 讓 `DynamoDBBackend` 實作 7 個原 local-only 方法（`get_active_watchlist_symbols`、`get_watchlist_earnings_for_date`、`get_chat_watchlist_earnings`、`unnotified_symbols`、`mark_notified`、`get_company_summary`、`upsert_company_summary`）。它們現在是跨 backend 共用，故**升格進 `StorageBackend` Protocol**。ADR-0003 的原則（Protocol 只涵蓋跨 backend 共用方法）不變，僅因這些方法變成共用而納入。news / market_summaries（PR-3）與 `export_rows` 維持 local-only、不進 Protocol。

### 8. `get_watchlist(None)` 過渡分支
PR-1 不改 `scheduler_handler.py`，`_notify_live()` 仍呼叫 `get_watchlist()`（無 chat_id）期待全域 `DEFAULT_WATCHLIST`。故 `DynamoDBBackend.get_watchlist(chat_id=None)` 保留「`None` → 回 `DEFAULT_WATCHLIST`」過渡分支，`chat_id` 非 None 才走 per-subscriber query。PR-2 移除 `_notify_live` 後一併移除此分支。

### 9. upload_local_store 只搬 watchlist
`db/upload_local_store.py` 新增上傳 `watchlist`（真實使用者資料，cutover 後 production 需即用），**不上傳** earnings_calendar / earnings_notifications / company_summaries（暫存快取，sync cron 重建）。

## Consequences
正面：
- Production 取得與 local 相同的 DB-first 能力，PR-2 可直接用同一組 backend 方法跑 sync/notify，無 backend 專屬分支。
- 表結構一對一對映 local，行為 parity 容易測試與推理。
- conditional put 冪等與既有 dedup pattern 一致，維運心智模型單一。

負面 / trade-off：
- notify by date 需「逐 active subscriber query watchlist」，呼叫數隨訂閱者數線性成長；單一/少量使用者可接受，規模大時再引入 symbol GSI 反查（見替代方案）。
- earnings_notifications 不上傳 → cutover 當天對隔日財報可能一次重複通知；單一使用者、一次性、可用切換時機規避。
- `get_watchlist(None)` 過渡分支是暫時的不一致（同方法對 None 與非 None 語意不同），已明確標記 PR-2 清除。
- Decimal↔float 轉換是 boto3 resource 的必要樣板，散在寫/讀兩端；集中在 backend 內以隔離。

## Alternatives considered
- **Single-table design**：一張表塞多實體，query 更省。但與既有三表風格不一致、GSI/SK 設計複雜、對此規模無效益，違反「簡單優先」。
- **watchlist 加 symbol GSI 做反查**：notify 可「由 symbol 反查追蹤者」少一層迴圈。但當前 join 用不到、GSI 增加寫入成本與維護面；訂閱者規模小，逐 subscriber query 已足夠。保留為未來擴充點。
- **冪等合併成單一 claim（unnotified 檢查時就 conditional put）**：少一次讀。但改變 scheduler 兩段式呼叫形狀、與 local 語意分岔，且「先算要送誰→送→標記」的順序對「送失敗不標記」的正確性更清楚。維持兩段式。
- **DynamoDBBackend 補齊全部 local-only 方法（含 news/market_summaries）**：一次到位。但 news/market_summaries 是 PR-3 範圍，提前補會寫入未被使用的路徑，違反「不為不可能情境寫程式」；分 PR 保持每個 PR 可獨立驗收。
- **earnings_notifications 也上傳**：可完全消除 cutover 重複通知。但要多維護一條上傳路徑處理 TTL/複合 SK，為單日單次邊界過度投資；用切換時機規避更省。
