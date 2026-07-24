# ADR-0005: DynamoDB 為 Telegram 輸入的 source of truth，改為每日下行同步

- **Date**: 2026-07-24
- **Status**: Accepted
- **關聯**: 取代 ADR-0004 §9「upload_local_store 只搬 watchlist」及其 2026-07-24 update（改為「也上傳 watchlist/earnings_calendar/company_summaries/news」）中**關於 watchlist / subscribers 上行**的部分。ADR-0004 其餘（表設計、application-side join、冪等、TTL、Protocol 範圍）維持 Accepted 不變。

## Context
Phase 3 已把 Telegram webhook 切回雲端 API Gateway。使用者的 `/watch` `/unwatch` `/subscribe` `/unsubscribe` 由**雲端 processor** 寫入 DynamoDB（watchlist、subscribers）。但本地 launchd 每日任務（10:30 台北）讀寫的是本地 sqlite，雲端寫入不會反映在每日通知裡；更糟的是 10:30 第四步 `upload_local_store --write` 會把 stale 的本地 watchlist 上傳，**把使用者已 `/unwatch` 的股票在雲端復活**。

先前 ADR-0004（PR-1）的資料流假設是「本地為主、上行到雲端」，方向與 Phase 3 後的現實相反。需要一次定案資料流方向與同步語意——這是牽動 source of truth、資料流方向、失敗語意的決策，故立 ADR。

方向已於 2026-07-24 與使用者定案，本 ADR 記錄決定與理由，非重開討論。

## Decision

### 1. DynamoDB 為 Telegram 輸入的唯一 source of truth
所有「Telegram 輸入且需入庫」的資料（watchlist、subscribers；廣義含 conversations、dedup）以 DynamoDB 為準。本地 sqlite 對這些表是 **read-only mirror**。

### 2. 新增每日下行同步（DynamoDB → 本地 sqlite），完整覆蓋
每天 00:00（台北午夜）跑新模組 `agent.db.download_remote_store`，把 DynamoDB **watchlist + subscribers** 完整鏡射回本地：
- 先全量 scan（處理 `LastEvaluatedKey` 分頁）進記憶體，全部成功後才在**單一 sqlite transaction** 內 `DELETE` 全表 → `INSERT` scan 結果 → commit。
- 完整覆蓋語意：雲端刪掉的本地也刪。
- 只鏡射這兩張表；不鏡射 conversations（本地每日 pipeline 不讀，無實益）、dedup（雲端 webhook 專用）、以及本地產生的快取表。

### 3. 上行只搬本地產生的快取
`upload_local_store` 改為只上傳本地每日 pipeline 產生的快取：`earnings_calendar`、`company_summaries`、`news`。**移除** watchlist、subscribers、conversations、dedup 的上行：
- watchlist / subscribers：Telegram 輸入、雲端 source of truth；上行會用 stale 本地副本回寫覆蓋雲端（正是此次要修的 bug）。
- conversations / dedup：同屬雲端寫入的 Telegram 輸入；上行本地（可能是 dev/stale）副本會污染雲端對話歷史 / dedup，且對雲端無用。屬本 ADR 原則的一致延伸。

### 4. 失敗語意：atomic，不留半空表
本地每日 notify 依賴 watchlist/subscribers。下行以「scan 全成功 → 單一 transaction 覆蓋兩表」確保：scan 中途失敗則本地零改動；寫入中途失敗則整體 rollback。本地表永遠是「完整舊狀態」或「完整新狀態」，不會半空。

### 5. 空狀態採嚴格鏡射
scan 回 0 rows 即視為雲端該表確實為空、本地跟著清空。不加「回空就跳過覆蓋」保護，因為 DynamoDB scan 遇真錯會 raise（空即真空），且該保護會讓「全部 unwatch / 退訂」無法傳播、對已退訂者誤發通知，比清空更糟。「不留半空」針對寫入中途失敗（由 transaction 解決），與合法空狀態無關。

### 6. seed_watchlist_if_empty 不改
唯一有意義的 seed 路徑是雲端 processor `/subscribe` → seed 進 DynamoDB。本地每日 pipeline 不呼叫 seed；本地 seed 只在 dev 對 `local_server` `/subscribe` 時觸發，且下一次 00:00 下行整表覆蓋掉。故無 double-seed / 分岔風險，seed 邏輯維持不變。

### 7. 排程：獨立 00:00 launchd job
新增獨立 plist（`com.earnings-agent.downsync`）於 00:00 跑下行，與既有 10:30 上行分離。00:00 下行 → 10:30 用剛更新的本地 watchlist 抓財報並只上行快取。兩者相隔 10.5 小時、無 race。

## Consequences
正面：
- 修掉「10:30 上行用 stale watchlist 覆蓋雲端刪除」的 bug——上行不再碰 watchlist/subscribers。
- 雲端與本地對 watchlist/subscribers 每日對齊；使用者的 `/unwatch` `/unsubscribe` 隔天生效於本地通知。
- 資料流方向單一清楚：Telegram 輸入 → DynamoDB（權威）→ 本地 mirror；本地快取 → DynamoDB（僅快取）。

負面 / trade-off：
- 同步為每日粒度：00:00~10:30 之間雲端的改動要隔日才反映在本地。單一使用者、每日通知場景可接受。
- 新增一個 launchd 排程與 wrapper，維運面多一份 plist（路徑寫死本機，同既有 daily plist 限制）。
- 嚴格鏡射意味雲端誤刪會直接傳播到本地（無保護網）；單一使用者、可用雲端資料自身備援。

## Alternatives considered
- **併入 10:30 排程當第一步（下行→sync→notify→上行）**：單一排程、下行資料最新。但使用者定案要 00:00，且分離排程讓「輸入對齊」與「快取產生」職責清楚。採獨立 00:00。
- **保留 watchlist 上行、改雙向 merge**：可容忍兩邊各自改動。但需要 conflict resolution、時間戳裁決，對單一使用者過度工程；且 source of truth 一旦明確（DynamoDB），單向覆蓋最簡單正確。
- **下行也鏡射 conversations**：一致性更「完整」。但本地每日 pipeline 不讀 conversations，純屬把雲端歷史複製一份、增加維護面無收益，違反「簡單優先」。
- **空狀態保護（scan 回空不覆蓋）**：防雲端誤刪整表。但製造正確性破洞（退訂 / 清空 watchlist 無法傳播），且 scan 真錯會 raise 而非回空，保護反而有害。採嚴格鏡射。
- **只移除 watchlist 上行、保留 subscribers/conversations/dedup 上行**（最小改動）。但 subscribers/conversations/dedup 同屬雲端寫入的 Telegram 輸入，保留上行會用 stale 本地副本回寫污染雲端；一致移除才符合 source-of-truth 方向。
