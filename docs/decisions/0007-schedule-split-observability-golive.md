# ADR-0007: 排程拆 sync/notify + observability go-live

- **Date**: 2026-07-24
- **Status**: Accepted
- **關聯**:
  - **部分取代 ADR-0006 §5（單次 invocation 跑完整 pipeline）與 §7（單一 10:30 排程）**。ADR-0006 的核心方向（daily pipeline 上雲、直讀 DynamoDB、`_notify_live` 死碼移除、news 落庫 / market_summaries no-op、observability=失敗通知且與 schedule ENABLE 綁同一 PR）全部不變且被實作；本 ADR 只修正「執行切分」與「排程形狀」兩個執行層決策，並補上 ADR-0006 未定案的 retry 冪等與 alarm 細節。
  - 對應 spec `docs/specs/observability-golive.md`、contract `docs/contracts/observability-golive-sprint.md`、roadmap `docs/TASKS.md`（Roadmap v5 → PR-B）。

## Context

方向已於 2026-07-24 與使用者收斂定案（非重開討論）。ADR-0006 原設計是「單一 EventBridge 排程 10:30 台北 → 單次 Lambda invocation 依序跑 sync → notify → summary」。使用者定案把它拆成兩個時點：

- **08:00 台北**只跑「抓取/寫入」（sync earnings + news 抓取，Finnhub 直寫 DynamoDB）。
- **10:00 台北**只跑「讀庫 → Groq → Telegram」（財報通知 + 大盤摘要）。

拆分理由：讓 08:00 的抓取失敗有約 2 小時的自動重試 + 人工補救窗口，10:00 讀到的已是當日最新快取；也讓兩類外部依賴（Finnhub 寫 vs Groq/Telegram 送）在監控與重試上彼此隔離，一邊抖動不必然標記另一邊失敗。

本 PR 同時是 **go-live PR**：排程由 `DISABLED` 改 `ENABLED`，並依 ADR-0006 §6 硬需求把 observability（CloudWatch Alarm → SNS → email）與 ENABLE 綁在同一個 PR。

程式碼現況（PR-A 已在工作樹）核對：`market_summary.push_daily_summary()` 的結構是 `refresh_news()`（Finnhub 抓取寫入）→ `generate_market_summary()`（讀回 → Groq）→ 推播。故 news refresh **在** `push_daily_summary` 內——拆分時 notify mode 必須跳過 refresh，否則 08:00 與 10:00 會各抓一次 Finnhub。

## Decision

### 1. 排程拆兩個，同一 SchedulerFunction，用 Input payload 分流
兩個 `ScheduleV2` events 掛在同一個 `SchedulerFunction`，各帶 `Input`：

- `SyncSchedule`：`cron(0 8 * * ? *)`、`ScheduleExpressionTimezone: Asia/Taipei`、`Input: '{"mode": "sync"}'`。
- `NotifySchedule`：`cron(0 10 * * ? *)`、`Asia/Taipei`、`Input: '{"mode": "notify"}'`。

`handler(event, context)` 依 `event.get("mode")` 分流：

- `sync` → `sync_earnings()` + `refresh_news(days_back=1)`（皆 Finnhub 寫 DynamoDB）。
- `notify` → `_notify_local(tomorrow)`（財報通知）+ `push_daily_summary(refresh=False)`（大盤摘要，不再抓 Finnhub）。

兩個 mode 各自沿用 ADR-0006 §5 的「階段獨立 try/except，任一階段失敗 → 跑完所有階段後 re-raise」語意（失敗計入 Lambda `Errors`、已完成階段副作用保留）。

`market_summary.push_daily_summary` 新增 `refresh: bool = True` 參數：預設 True 保留本地 `make daily-summary`（launchd，未拆分）行為；雲端 notify mode 傳 `refresh=False`。本地 CLI（`python -m agent.scheduler_handler [sync]`、`python -m agent.market_summary`）行為不變。

### 2. 無 payload / 未知 mode → fail-fast（raise）
`handler` 在 `mode` 不是 `sync`/`notify`（含缺 payload）時 **raise `ValueError`**，不做「向後相容跑完整 pipeline」。

理由：go-live 後唯一的呼叫來源是這兩個帶 `mode` 的排程；缺 payload 或未知 mode 一定是設定漂移（例如某個排程掉了 `Input`）。此時 fail-fast → 計入 Lambda `Errors` → 觸發本 PR 新增的 alarm，讓誤設定當場可見，勝過在錯誤時點靜默跑完整 pipeline（例如 08:00 排程掉 payload 卻在 08:00 也推播通知）。代價是失去「手動無 payload invoke 跑全套」的便利，但需要手動全跑時分別 invoke 兩個 mode 即可，符合簡單優先。

### 3. 自動重試：EventBridge Scheduler RetryPolicy
兩個排程都設 `RetryPolicy: { MaximumRetryAttempts: 2, MaximumEventAgeInSeconds: 3600 }`。

理由：2 次重試足以吸收 Finnhub/Groq/Telegram 的短暫抖動；1 小時 event age 上限確保 (a) sync（08:00）的重試在 10:00 notify 之前結束、(b) notify 的重試不會拖進隔日。不另設 schedule 層 DLQ——Lambda `Errors` metric 已涵蓋每一次（含最終重試）失敗，alarm 是觀測管道；為單一使用者的每日任務再加一條 SQS DLQ + IAM 屬過度投資（已評估，未採）。

### 4. 大盤摘要推播補冪等 guard（重試安全）
啟用重試後，notify mode 的兩個推播階段裡：

- **財報通知**：已冪等（`earnings_notifications` 的 `unnotified_symbols` / `mark_notified`，send 成功才 mark）。重試安全。
- **大盤摘要**：原本無冪等。整個 invocation 重試會 re-run 兩個階段，導致「摘要階段已成功、但財報階段失敗觸發重試」或「摘要送給 A 成功、送 B 失敗觸發重試」時，摘要被**重複推播**。

故補 per-(subscriber, 日期) 的摘要冪等 guard，鏡射財報通知的模式：

- 新增 Protocol 方法 `summary_notified(chat_id, summary_date) -> bool` 與 `mark_summary_notified(chat_id, summary_date) -> None`。
- **複用既有 `earnings_notifications` 表**（不新增表 / GSI / IAM）：DynamoDB 側 `notify_key = f"market-summary#{summary_date}"` 的 conditional put；本地 sqlite 側以 sentinel `symbol="__market_summary__"`、`earning_date=summary_date` 複用同表。該表在概念上是「通知去重」store，用不同 key 承載 earnings 與 summary 兩類通知是合理的一般化；scheduler 對此表已有 CRUD 權限。
- guard 放在共用的 `push_daily_summary`（非只在雲端路徑），維持 ADR-0006「雲端與本地共用同一條 summary 推播程式碼」；本地每日只跑一次，guard 實際為 no-op、無害。
- 只 guard **推播**：`generate_market_summary`（1 次 Groq）在重試時仍會重跑，不去重（成本極低、單一使用者），但不會重複 push。

### 5. 可觀性：CloudWatch Alarm → SNS Topic → email
- `PipelineAlarmsTopic`（SNS）+ email subscription，endpoint 取自新增的 SAM `AlarmEmail` Parameter（**不 hardcode**；部署時以 `--parameter-overrides` 或 samconfig 提供，真實信箱不進版控）。
- Alarms（`AWS/Lambda` metric，dimension `FunctionName`）→ `AlarmActions: [PipelineAlarmsTopic]`：
  - `SchedulerFunction` `Errors >= 1`（Sum / 300s / 1 period）。
  - `ProcessorFunction` `Errors >= 1`（同上）。
  - `SchedulerFunction` `Duration >= 250000ms`（Maximum / 300s / 1 period）——300s timeout 的早期預警。Lambda timeout 本身會計入 `Errors`（已被上一條涵蓋），此條的增值是「逼近但尚未 timeout」的早期可見性；對每日任務值得，且成本僅一條 alarm。
- 走 CloudWatch metric（Lambda 層），即使 handler import error / timeout 也發得出，與 app code 解耦（ADR-0006 §6）。Telegram 送信失敗在 code 內被 try/except 吞掉、不計入 `Errors`，本 PR **不**為它建 log metric filter alarm（單一使用者、整條 pipeline 真失敗仍會 raise；列為未採，未來需要再補）。

### 6. 排程在 template 內 ENABLE（go-live）
兩個 `ScheduleV2` events 皆 `State: ENABLED`。observability 與 ENABLE 同 PR（ADR-0006 §6 硬需求）。cutover 的雙推風險（雲端 ENABLE 期間本地 launchd 仍跑 → 冪等 store 分屬兩 backend、各推一次）由 Phase 3 ops「**先停本地 launchd → 再部署含 ENABLE 的 template**」規避，屬本 PR 範圍外的維運動作（見 Consequences 與 TASKS.md）。

## Consequences

正面：
- 抓取與推播在時間、監控、重試上隔離；08:00 抓取失敗有窗口自動重試/補救，10:00 讀到最新快取。
- 重試對兩個 mode 都安全（earnings + summary 皆冪等），可放心 ENABLE retry。
- 失敗（含誤設定 mode、逼近 timeout）第一次雲端 run 起即可透過 email 觀測。
- 摘要冪等複用既有表，未新增 infra。

負面 / trade-off：
- 大盤摘要冪等複用 `earnings_notifications` 表 + sentinel key，語意上略微 overload（表名仍偏 earnings）。以文件與方法命名界定，避免改表名的 scope 擴張；未來若通知類型變多可重新命名為通用 notifications 表。
- fail-fast 於缺 payload 時不再跑完整 pipeline：手動全跑需分別 invoke 兩個 mode。
- 兩個排程 = 兩個 EventBridge Scheduler 資源 + 兩個 Lambda permission，template 略增；換來時間隔離，值得。
- cutover 仍需人工協調（停 launchd ↔ 部署 ENABLE），雙推窗口靠 ops 順序規避，非程式保證。

## Alternatives considered
- **維持 ADR-0006 §5/§7 單排程單次 invocation（10:30 一次跑完）**：更少資源。但抓取與推播綁死同一時點、同次失敗互相牽連，且無「先抓、後推」的補救窗口。使用者已定案拆分，不採。
- **缺 payload 時向後相容跑完整 pipeline**：手動 invoke 較方便。但會在誤設定（掉 payload）時靜默於錯誤時點跑完整流程（含推播），與 go-live 的可觀測性目標相悖。採 fail-fast。
- **摘要不加冪等、接受重試偶發重複推播**：最省 code。但 go-live 刻意啟用 retry，重複摘要會實際發生（每日 cron，數月尺度必遇），對每日使用者是可感知的騷擾；複用既有表的 guard 成本有限。不採。
- **摘要冪等另建 `market-summary-notifications` 表**：語意最乾淨。但需新增表 + IAM + template + conftest，違反簡單優先且與 ADR-0006「不為可避免的東西增表」一致性不符。採「複用既有表 + sentinel key」。
- **retry 用更大次數 / 更長 event age**：更韌。但單一使用者每日任務，2 次 / 1 小時已足夠且能保證 sync 重試在 notify 前收斂、notify 重試不跨日。不採更激進值。
- **schedule 層 SQS DLQ + Telegram 失敗 log metric alarm**：觀測更完整。但 Lambda `Errors` alarm 已覆蓋 invocation 失敗，Telegram 失敗在單一使用者下由整條 pipeline re-raise 兜底；額外 infra 屬過度投資，列為未採 / 未來再補。
