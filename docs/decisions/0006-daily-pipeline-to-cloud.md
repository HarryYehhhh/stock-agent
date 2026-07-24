# ADR-0006: daily pipeline 上雲端（修正 hybrid local-first 方向）

- **Date**: 2026-07-24
- **Status**: Accepted
- **關聯**:
  - 修正 **ADR-0005** 的方向假設。ADR-0005 確立「DynamoDB 為 Telegram 輸入的 source of truth + 每日 00:00 下行」，但其前提是 daily pipeline **留在本地 launchd**、雲端 `SchedulerFunction` 刻意 `DISABLED`。本 ADR 反轉該前提：daily pipeline 搬上雲端 EventBridge。ADR-0005 的「DynamoDB 為 source of truth」核心不變且被強化；被取代的只是「pipeline 執行位置在本地」與「需要每日下行同步」兩點（見下）。
  - 延伸/部分取代 **ADR-0004 §7**（「news / market_summaries 維持 local-only、不進 Protocol」）與其 2026-07-24 update（market_summaries 維持 local-only）。

## Context

方向已於 2026-07-24 與使用者定案（走「B：搬上雲端」，不再停在 hybrid local-first）。本 ADR 記錄決定與理由，非重開討論。

現況（ADR-0005 hybrid local-first）：
- 雲端：webhook + processor 寫 DynamoDB，DynamoDB 為 watchlist/subscribers 的 source of truth。雲端 `SchedulerFunction` `State: DISABLED`。
- 本地 launchd：00:00 下行（DynamoDB → sqlite 鏡射）＋ 10:30 pipeline（sync → notify → summary，再上行快取）。

問題：daily pipeline 綁在單一台 macOS（launchd plist 路徑寫死本機），維運面脆弱、無雲端可觀測性、且需要「00:00 下行」這條額外同步鏈只為了讓本地 sqlite 追上雲端輸入。既然 DynamoDB 已是 source of truth 且所有 pipeline 需要的 backend 方法在 PR-1（ADR-0004）已於 DynamoDBBackend 補齊，把 pipeline 搬上雲端 Lambda 直接讀寫 DynamoDB 可一次消除本地依賴與下行同步鏈。

## Decision

### 1. daily pipeline 由雲端 SchedulerFunction 執行，直讀 DynamoDB
雲端 `SchedulerFunction` 每日跑完整 pipeline，全程走 `DynamoDBBackend`（不碰 sqlite）：
`sync_earnings`（讀 DynamoDB watchlist → Finnhub → 寫 earnings_calendar 快取）→ 財報通知（讀快取 → 每公司 Groq 摘要快取 → 冪等推播 active 訂閱者）→ 每日科技股大盤摘要推播。

因 DynamoDB 已是 source of truth，雲端 pipeline 直接讀權威資料，**不需要 ADR-0005 的 00:00 下行同步**。下行同步在 cutover 後失去唯一用途（餵本地 pipeline）；其停用屬 ops 動作（停本地 launchd），不在本 code sprint。

### 2. 移除 `_notify_live` 與 `get_watchlist(None)` 過渡分支（死碼轉正）
ADR-0004 §8 保留的 `scheduler_handler._notify_live()`（打全域 `DEFAULT_WATCHLIST`、每 subscriber 一次 Groq）與 `DynamoDBBackend.get_watchlist(chat_id=None)` 的 `DEFAULT_WATCHLIST` 分支，是「PR-2 之前」的過渡。雲端 pipeline 改走與本地同一條 per-subscriber、快取化、冪等的 `_notify_local` 路徑（該路徑的 backend 方法在 DynamoDB 已實作），故 `_notify_live` 與 `get_watchlist(None)` 分支移除。`_run()` 不再依 `selected_backend_name()` 分岔。

### 3. 快取策略：news 落 DynamoDB、market_summaries 不落
雲端 pipeline 需要的持久化快取分兩類處置：
- **earnings_calendar / company_summaries / earnings_notifications**：維持 DynamoDB 持久化（PR-1 已實作，行為 parity）。sync 寫、notify 讀、冪等靠 earnings_notifications conditional put。
- **news**：`DynamoDBBackend` 補 `insert_news`（headline 為 PK，put_item 去重）＋ `list_news_since`（查既有 `published-at-index` GSI，`bucket="news"` AND `published_at >= from`）。`NewsTable` 已存在 template、news 已由 #22 上行；補齊讀寫讓 `push_daily_summary` 在雲端與本地走同一條 `refresh_news → insert_news → list_news_since → select_news` 路徑。為維持 `generate_market_summary` 不改，`list_news_since`（DynamoDB）回傳列帶 `id`（值＝headline）。
- **market_summaries**：**雲端不持久化**。它是純稽核記錄，production 無任何路徑讀回（僅測試讀），且 `news_ids` 稽核鏈綁 sqlite autoincrement id、無法對映 DynamoDB（headline PK）。`DynamoDBBackend.insert_market_summary` 為 no-op（回 `None`）。不為只寫不讀的稽核 log 新增一張表 + GSI + IAM，違反「簡單優先／不為不可能情境寫程式」。未來若雲端需稽核再補表。

### 4. 三個方法升格進 StorageBackend Protocol
`insert_news` / `list_news_since` / `insert_market_summary` 因兩個 backend 都實作而成為跨 backend 共用，升格進 `StorageBackend` Protocol（取代 ADR-0004 §7 將它們列為 local-only）。ADR-0003 原則（Protocol 只涵蓋跨 backend 共用方法）不變。`export_rows` 維持 local-only（僅 upload 工具用）。

### 5. Lambda 單次執行完成整條 pipeline，順序容錯後標記失敗
`handler()` 於單次 invocation 依序跑 sync → notify → summary。各階段獨立 try/except：一階段失敗記 log 不阻擋後續階段（summary 失敗不影響已送出的 earnings 通知）；但只要任一階段失敗，handler 在跑完所有階段後 **re-raise**，使該次 invocation 計入 Lambda `Errors` metric → 觸發告警（見 §6）。單一使用者規模下，單次 run 的 Groq 呼叫數（每家報財報公司 1 次＋大盤摘要 1 次，通常 1–4 次）與 Finnhub 呼叫（sync 每 symbol 1 次＋摘要 ~12 次）遠在 300s Lambda timeout 與免費 tier rate limit 內。

### 6. 可觀性：失敗通知（CloudWatch Alarm → SNS → Email），與 app code 解耦
以「失敗通知」而非 dashboard 為形式。至少對 `SchedulerFunction` 與 `ProcessorFunction` 的 Lambda `Errors` metric 設 CloudWatch Alarm → SNS Topic → email subscription。SNS/alarm 走 CloudWatch metric（Lambda 層），即使 scheduler 整個 import error / timeout 也發得出，與 app code 解耦。email 位址以 SAM parameter 參數化（可搭 SSM），不 hardcode。此為 pipeline 上雲的硬前提：**第一次雲端 daily run（＝schedule ENABLE）起就要能觀測失敗**，故 observability 與 schedule ENABLE 綁同一個 PR（見 TASKS.md roadmap PR-B）。

### 7. 排程：EventBridge 在 template 內 ENABLE，時區用 Asia/Taipei
`SchedulerFunction` 的 `ScheduleV2` 由 `DISABLED` 改 `ENABLED`（避免 deploy 漂移），並用 `ScheduleExpressionTimezone: Asia/Taipei` + `cron(30 10 * * ? *)` 對齊現行本地 10:30 台北（Phase 1 已實地驗證的時間），免手動 UTC 換算、self-documenting。ENABLE 與停本地 launchd 的 cutover 協調屬 ops（見 Consequences 的雙推風險）。

## Consequences

正面：
- daily pipeline 脫離單一台 macOS/launchd，跑在雲端、可觀測、免每日下行同步鏈。
- 雲端與本地共用同一條 per-subscriber/快取/冪等 pipeline 程式碼，移除 `_notify_live` 分岔，維運心智模型單一。
- 三個快取方法 backend parity，moto 可測。

負面 / trade-off：
- **cutover 雙推風險**：earnings/summary 的冪等（earnings_notifications）與快取分屬兩個 backend store（雲 DynamoDB vs 本地 sqlite）、不共用。若雲端 schedule ENABLE 期間本地 launchd 仍在跑，兩邊各自推播 → 使用者收到重複通知。故 cutover 必須「停本地 launchd」與「部署含 ENABLE 的 template」協調進行（ops，由 infra-engineer runbook 規範）。
- market_summaries 雲端無稽核持久化：失去「摘要 ↔ 來源 news」的可追溯。單一使用者、摘要即時推播即可，接受。
- 單次 Lambda 跑完整 pipeline：串行時間較長（但遠低於 300s），且 sync/notify/summary 耦合在一次 invocation，任一階段的外部 API 抖動都可能標記整次失敗（由階段容錯 + 最後 re-raise 平衡：已完成階段的副作用保留，失敗仍可觀測）。

## Alternatives considered
- **維持 hybrid local-first（ADR-0005 現狀）**：不動。但使用者已定案要上雲，且本地 launchd 綁單機、無雲端可觀測性、下行同步鏈純為餵本地。不採。
- **market_summaries 也建 DynamoDB 表 + insert_market_summary 真實作**：稽核完整。但 production 從不讀回、news_id 稽核鏈無法對映 DynamoDB，為只寫不讀的 log 增表 + GSI + IAM 屬過度投資，違反簡單優先。採 no-op，未來需要再補。
- **news 雲端改 live-fetch 不落庫（in-memory 傳遞）**：省一張表的讀寫。但需重構 `refresh_news`/`select_news` 的「寫庫→讀回」結構、與本地路徑分岔、失去 headline 去重的重試冪等；NewsTable 既已存在且被上行填充，補讀寫成本更低且保 parity。採落庫。
- **拆成「earnings 雲端」「summary 雲端」兩個 PR**（比照原 PR-2/PR-3）：更小顆粒。但 PR-1 已把 earnings 路徑的 backend 方法補齊，earnings 半邊只剩「移除死碼 + 路由」幾乎無獨立可交付價值，且 summary 半邊依賴同一個 handler 組裝；拆開會讓 earnings PR 在 schedule DISABLED 下不產生任何可觀測行為。合併為「完整化雲端 pipeline」單一 PR 更內聚（見 TASKS.md roadmap PR-A）。
- **observability 獨立成第三個 PR**：職責更分。但「第一次雲端 run 起可觀測」是硬需求，而首次 run＝schedule ENABLE，故 ENABLE 與 alarm/SNS 必須同一 PR（或 observability 在前），合併進 PR-B。
