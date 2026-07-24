# Spec: Observability + schedule ENABLE（PR-B，go-live）

> 方向決策：`docs/decisions/0007-schedule-split-observability-golive.md`（ADR-0007，部分取代 ADR-0006 §5/§7）
> Roadmap 定位：`docs/TASKS.md` → Roadmap v5 → PR-B。相依：PR-A（`docs/specs/cloud-daily-pipeline.md`，已合入工作樹）。

## Goal
把雲端 daily pipeline 正式上線：排程拆成 08:00 sync + 10:00 notify 兩個時點（mode 分流）、啟用自動重試與摘要冪等、加上 CloudWatch Alarm → SNS → email 的失敗通知，並將排程由 `DISABLED` 改 `ENABLED`。observability 與 ENABLE 綁同一 PR（ADR-0006 §6 硬需求）。

## User stories
- As the 系統維運者, I want 雲端排程一啟用就能在失敗時收到 email, so that daily pipeline 上線後不需盯 log 也能得知抓取/推播失敗。
- As the 系統維運者, I want 抓取（08:00）與推播（10:00）拆成兩個時點且各自可自動重試, so that 抓取失敗有補救窗口、10:00 讀到最新快取，且一邊抖動不牽連另一邊。
- As a 訂閱者, I want 即使某次 run 部分失敗被自動重試, so that 我不會因重試而收到重複的大盤摘要或財報通知。

## Acceptance criteria

### 排程 mode 分流
- [ ] `scheduler_handler.handler(event, context)` 依 `event.get("mode")` 分流：
  - `"sync"` → 執行 `sync_earnings()` 與 `refresh_news(days_back=1)`（皆 Finnhub 寫 DynamoDB）。
  - `"notify"` → 執行 `_notify_local(tomorrow)` 與 `push_daily_summary(refresh=False)`。
  - 其它值或缺 `mode` → raise（fail-fast，計入 Lambda `Errors`），不執行任何階段。
- [ ] 每個 mode 內各階段獨立 try/except：單一階段失敗記 log 不阻擋後續；任一階段失敗則跑完該 mode 所有階段後 re-raise。全成功回 `{"statusCode": 200}`。
- [ ] `market_summary.push_daily_summary` 新增 `refresh: bool = True`：`refresh=True` 時照舊先 `refresh_news`；`refresh=False` 時跳過抓取、直接 `generate_market_summary` + 推播。預設 True。
- [ ] 本地不變：`python -m agent.scheduler_handler sync` / `python -m agent.scheduler_handler`（notify）/ `python -m agent.market_summary`（`push_daily_summary()` 預設 refresh=True）與對應 make targets 行為與簽章不變。

### 大盤摘要冪等 guard
- [ ] 新增 `StorageBackend` Protocol 方法 `summary_notified(chat_id, summary_date) -> bool` 與 `mark_summary_notified(chat_id, summary_date) -> None`，兩個 backend 皆實作（複用既有 `earnings_notifications` 表，見 ADR-0007 §4，不新增表）。
- [ ] `push_daily_summary` 推播迴圈以 `summary_date = date.today().isoformat()` 為 key：已 `summary_notified` 的訂閱者跳過；send 成功後 `mark_summary_notified`。同一天重跑不重複推播。
- [ ] 財報通知冪等（`earnings_notifications` / `unnotified_symbols` / `mark_notified`）不變。

### stale 標註（沿用 ADR-0006 決策 #3）
- [ ] notify mode 下若 08:00 sync 徹底失敗使 earnings 快取逾 `MAX_DATA_AGE_HOURS`（24h），財報通知仍照發並在訊息開頭帶既有 `_stale_banner`（含最後更新時間）。既有 `_is_stale`/`_stale_banner` 邏輯不需重寫，只需確認在拆分後 notify path 仍生效並有測試覆蓋。

### IaC：排程、重試、SNS、alarm、ENABLE
- [ ] `infra/template.yaml` `SchedulerFunction` 以兩個 `ScheduleV2` events 取代現有單一 `Daily` event：
  - `SyncSchedule`：`ScheduleExpression: cron(0 8 * * ? *)`、`ScheduleExpressionTimezone: Asia/Taipei`、`Input: '{"mode": "sync"}'`、`State: ENABLED`、`RetryPolicy`。
  - `NotifySchedule`：`cron(0 10 * * ? *)`、`Asia/Taipei`、`Input: '{"mode": "notify"}'`、`State: ENABLED`、`RetryPolicy`。
  - 兩者 `RetryPolicy: { MaximumRetryAttempts: 2, MaximumEventAgeInSeconds: 3600 }`。
- [ ] 新增 SAM Parameter `AlarmEmail`（Type String，無 Default），供 SNS email subscription 使用；真實信箱以部署時 `--parameter-overrides` / samconfig 提供，不寫進 template。
- [ ] 新增 `PipelineAlarmsTopic`（`AWS::SNS::Topic`）與 email subscription（`Protocol: email`、`Endpoint: !Ref AlarmEmail`）。
- [ ] 新增 CloudWatch Alarms（`AWS::CloudWatch::Alarm`），`AlarmActions: [!Ref PipelineAlarmsTopic]`：
  - `SchedulerFunction` `Errors >= 1`（Namespace `AWS/Lambda`、Dimension `FunctionName=!Ref SchedulerFunction`、Sum / Period 300 / 1 period / `TreatMissingData: notBreaching`）。
  - `ProcessorFunction` `Errors >= 1`（同上，`FunctionName=!Ref ProcessorFunction`）。
  - `SchedulerFunction` `Duration >= 250000`（Maximum / Period 300 / 1 period）。
- [ ] 移除舊的 `Daily` event 與其 `State: DISABLED`、`ScheduleExpression: cron(0 0 * * ? *)`（UTC）。PR-A 加的 `NewsTable` IAM / env 不動。

## Non-goals
- 不 `make deploy`、不做部署後雲端驗證、不確認 email subscription（需點信中連結）——屬 Phase 3 ops（infra-engineer / 使用者）。
- 不停用本地 launchd、不動 downsync 模組——屬 Phase 3 ops。
- 不改 PR-A 已完成的 DynamoDB backend news / market_summaries 方法、earnings 路徑、webhook / processor 行為。
- 不建 schedule 層 SQS DLQ、不建 Telegram-送信-失敗的 log metric alarm（ADR-0007 §5 已評估未採）。
- 不新增 market summary 冪等專用表（複用 `earnings_notifications`）。
- 不把真實 alarm email 寫進版控。

## Open questions
- 無阻擋性問題。排程時間（08:00 sync / 10:00 notify Asia/Taipei）、retry 值（2 / 3600s）、alarm 門檻、缺 payload=fail-fast、摘要冪等複用既有表——皆於 ADR-0007 定案。`AlarmEmail` 實值由 Phase 3 cutover 時提供。
