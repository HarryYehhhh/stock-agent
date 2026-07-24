# Contract: Observability + schedule ENABLE（PR-B，go-live）

對應 spec: docs/specs/observability-golive.md
方向決策: docs/decisions/0007-schedule-split-observability-golive.md（ADR-0007）
相依: PR-A（docs/contracts/cloud-daily-pipeline-sprint.md，已合入工作樹）

> 名詞對照（見 CLAUDE.md）：應用程式碼在 `src/agent/`；IaC 在 `infra/template.yaml`；測試在 `tests/`；測試指令 `PYTHONPATH=src uv run pytest tests/ -v`。DynamoDB 測試用 `moto`（fixtures 見 `tests/conftest.py`，已建含 `earnings_notifications`(PK chat_id, SK notify_key)、`news`、`subscribers` 等表）；Groq / Finnhub / Telegram 用 `pytest-mock` / monkeypatch。

## Deliverables

### D1. handler mode 分流（`src/agent/scheduler_handler.py`）
- [ ] 改寫 `handler(event, context)`：讀 `mode = event.get("mode") if isinstance(event, dict) else None`。
  - `mode == "sync"` → `_run_sync()`。
  - `mode == "notify"` → `_run_notify()`。
  - 否則 → `raise ValueError(f"unknown scheduler mode: {mode!r}; expected 'sync' or 'notify'")`（在跑任何階段之前）。
- [ ] 新增 `_run_sync()`：兩個獨立 try/except 階段 `sync_earnings()`、`refresh_news(days_back=1)`；任一失敗記 `logger.exception` 且不阻擋另一階段，跑完後若曾失敗 `raise`，全成功回 `{"statusCode": 200}`。
- [ ] 新增 `_run_notify()`：兩個獨立 try/except 階段 `_notify_local(tomorrow)`（`tomorrow = (date.today()+timedelta(days=1)).isoformat()`）、`push_daily_summary(refresh=False)`；容錯 + 最終 raise 語意同上。
- [ ] import：`from agent.market_summary import push_daily_summary, refresh_news`（`refresh_news` 為新增 import）。
- [ ] 保留 `_run()`（本地 `__main__` notify 仍用）；`_run_notify()` 可重用 `_run()` 作為其 earnings 階段。`__main__` 區塊不改。
- [ ] 更新模組 docstring：說明雲端 handler 依 `mode` 分流（sync=抓取寫入 / notify=讀庫→Groq→推播），並述明缺/未知 mode fail-fast。

### D2. `push_daily_summary` 支援跳過 refresh（`src/agent/market_summary.py`）
- [ ] `push_daily_summary(refresh: bool = True) -> None`：`if refresh: refresh_news(days_back=1)`，其餘不變。預設 True，本地 `__main__` 與 `make daily-summary` 行為不變。
- [ ] 推播迴圈補冪等 guard（見 D3 方法）：`summary_date = date.today().isoformat()`；迴圈內 `if get_backend().summary_notified(chat_id, summary_date): continue`；`send_message` 成功後 `get_backend().mark_summary_notified(chat_id, summary_date)`（send 失敗照舊記 log、不 mark、不中斷其他訂閱者）。

### D3. 摘要冪等 guard（`src/agent/db/backend.py`，複用 `earnings_notifications` 表）
- [ ] `StorageBackend` Protocol 新增：
  - `def summary_notified(self, chat_id: str, summary_date: str) -> bool: ...`
  - `def mark_summary_notified(self, chat_id: str, summary_date: str) -> None: ...`
  - Protocol docstring 補一句：這兩個方法複用 notification-idempotency 表承載每日大盤摘要的推播去重（ADR-0007 §4）。
- [ ] `DynamoDBBackend`：用 `self._earnings_notifications`，`notify_key = f"market-summary#{summary_date}"`。
  - `summary_notified`：`get_item(Key={"chat_id": chat_id, "notify_key": notify_key})`，回 `"Item" in resp`。
  - `mark_summary_notified`：`put_item(Item={..., "ttl": int(time.time()) + EARNINGS_NOTIFICATIONS_TTL_SECONDS}, ConditionExpression="attribute_not_exists(notify_key)")`，吞 `ConditionalCheckFailedException`（對齊既有 `mark_notified`）。
- [ ] `LocalSQLiteBackend`：複用 `earnings_notifications` 表，sentinel `symbol = "__market_summary__"`、`earning_date = summary_date`。
  - `summary_notified`：`SELECT 1 FROM earnings_notifications WHERE chat_id=? AND symbol='__market_summary__' AND earning_date=?`。
  - `mark_summary_notified`：`INSERT OR IGNORE ...`（`sent_at = now`）。

### D4. IaC — 排程拆分 + ENABLE + RetryPolicy（`infra/template.yaml`）
- [ ] 刪除 `SchedulerFunction` 現有 `Events.Daily`（`cron(0 0 * * ? *)` / UTC / `State: DISABLED`）。
- [ ] 新增兩個 events：
  ```yaml
  Events:
    SyncSchedule:
      Type: ScheduleV2
      Properties:
        ScheduleExpression: "cron(0 8 * * ? *)"
        ScheduleExpressionTimezone: Asia/Taipei
        State: ENABLED
        Input: '{"mode": "sync"}'
        RetryPolicy:
          MaximumRetryAttempts: 2
          MaximumEventAgeInSeconds: 3600
    NotifySchedule:
      Type: ScheduleV2
      Properties:
        ScheduleExpression: "cron(0 10 * * ? *)"
        ScheduleExpressionTimezone: Asia/Taipei
        State: ENABLED
        Input: '{"mode": "notify"}'
        RetryPolicy:
          MaximumRetryAttempts: 2
          MaximumEventAgeInSeconds: 3600
  ```
- [ ] 不動 PR-A 加的 `NewsTable` IAM / `NEWS_TABLE` env、不動 scheduler 其餘 policies（`earnings_notifications` 已有 CRUD，摘要 guard 複用該表，無需新增 IAM）。

### D5. IaC — SNS + Parameter + Alarms（`infra/template.yaml`）
- [ ] `Parameters` 新增：
  ```yaml
  AlarmEmail:
    Type: String
    Description: Email address subscribed to pipeline failure alarms (set at deploy time; do not commit real value).
  ```
- [ ] 新增資源：
  ```yaml
  PipelineAlarmsTopic:
    Type: AWS::SNS::Topic
    Properties:
      TopicName: !Sub "${AppName}-pipeline-alarms"

  AlarmEmailSubscription:
    Type: AWS::SNS::Subscription
    Properties:
      TopicArn: !Ref PipelineAlarmsTopic
      Protocol: email
      Endpoint: !Ref AlarmEmail

  SchedulerErrorsAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AppName}-scheduler-errors"
      Namespace: AWS/Lambda
      MetricName: Errors
      Dimensions:
        - Name: FunctionName
          Value: !Ref SchedulerFunction
      Statistic: Sum
      Period: 300
      EvaluationPeriods: 1
      Threshold: 1
      ComparisonOperator: GreaterThanOrEqualToThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref PipelineAlarmsTopic]

  ProcessorErrorsAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AppName}-processor-errors"
      Namespace: AWS/Lambda
      MetricName: Errors
      Dimensions:
        - Name: FunctionName
          Value: !Ref ProcessorFunction
      Statistic: Sum
      Period: 300
      EvaluationPeriods: 1
      Threshold: 1
      ComparisonOperator: GreaterThanOrEqualToThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref PipelineAlarmsTopic]

  SchedulerDurationAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: !Sub "${AppName}-scheduler-duration"
      Namespace: AWS/Lambda
      MetricName: Duration
      Dimensions:
        - Name: FunctionName
          Value: !Ref SchedulerFunction
      Statistic: Maximum
      Period: 300
      EvaluationPeriods: 1
      Threshold: 250000
      ComparisonOperator: GreaterThanOrEqualToThreshold
      TreatMissingData: notBreaching
      AlarmActions: [!Ref PipelineAlarmsTopic]
  ```
- [ ] `infra/samconfig.toml`：以「註解」示範 `AlarmEmail` override 寫法（不填真實信箱），例如在 `[default.deploy.parameters]` 上方加註解說明部署時用 `--parameter-overrides AlarmEmail=<email>` 或在此檔加 `parameter_overrides = "AlarmEmail=you@example.com"`。**不得**填入真實地址。

## Behaviors（輸入 → 輸出）
- [ ] `handler({"mode": "sync"}, ctx)` → 呼叫 `sync_earnings` 與 `refresh_news`，不呼叫 `_notify_local` / `push_daily_summary`；全成功回 `{"statusCode": 200}`。
- [ ] `handler({"mode": "notify"}, ctx)` → 呼叫 `_notify_local` 與 `push_daily_summary(refresh=False)`，不呼叫 `sync_earnings` / `refresh_news`；回 `{"statusCode": 200}`。
- [ ] `handler({}, ctx)` / `handler({"mode": "foo"}, ctx)` → raise `ValueError`，不呼叫任何 pipeline 階段。
- [ ] mode 內某階段拋例外 → 同 mode 其餘階段仍執行（副作用保留），該 mode 最終 raise。
- [ ] `push_daily_summary(refresh=False)` → 不呼叫 `refresh_news`，仍 `generate_market_summary` + 推播。
- [ ] `push_daily_summary` 對同一 `summary_date` 連跑兩次 → 每個 active 訂閱者只被 `send_message` 一次（第二次被 `summary_notified` 跳過）。
- [ ] notify path 在 earnings 快取 stale（>24h）時 → 財報通知訊息開頭含 `_stale_banner`。

## Out of scope（本 sprint 不做）
- `make deploy`、部署後雲端驗證、email subscription 確認、停本地 launchd、downsync 停用 → Phase 3 ops。
- schedule 層 SQS DLQ、Telegram 送信失敗的 log metric alarm、market summary 冪等專用表。
- 改 PR-A 的 news / market_summaries backend 方法、earnings 路徑、webhook / processor 邏輯。
- 把真實 alarm email 寫進版控。

## Tests required
以 pytest 覆蓋。DynamoDB 路徑用 `aws` fixture（moto）、本地路徑用 `local_store` fixture；外部呼叫（Finnhub / Groq / Telegram `send_message`）以 monkeypatch / mock 攔截。

- [ ] handler mode 分流：`sync` 只跑 `sync_earnings` + `refresh_news`；`notify` 只跑 `_notify_local` + `push_daily_summary(refresh=False)`（用 mock/monkeypatch 斷言各函式被呼叫 / 未被呼叫，並斷言 `push_daily_summary` 收到 `refresh=False`）。
- [ ] handler 缺 mode / 未知 mode → `pytest.raises(ValueError)`，且斷言所有 pipeline 階段都未被呼叫。
- [ ] handler 容錯：令某 mode 內某階段 mock 拋例外 → 斷言同 mode 其餘階段仍被呼叫、handler 最終 raise。
- [ ] `push_daily_summary(refresh=False)`：斷言 `refresh_news` 未被呼叫、`generate_market_summary` 與 `send_message` 仍被呼叫。
- [ ] 摘要冪等（兩個 backend 各一）：同一 `summary_date` 連呼 `push_daily_summary` 兩次 → `send_message` 對每個訂閱者只發一次；`summary_notified` 在 mark 後回 True、mark 前回 False。
- [ ] `summary_notified` / `mark_summary_notified` 單元（DynamoDB via moto + local sqlite）：mark 後 `summary_notified` True、不同日期互不干擾、與 earnings 的 `mark_notified`（同表）互不誤判（earnings 的 `unnotified_symbols` 不因摘要 sentinel 誤判、反之亦然）。
- [ ] stale 標註：notify path 下 earnings 快取 `updated_at` 逾 24h → 通知含 `_stale_banner`；未逾期則不含（沿用既有 `_is_stale` 測試模式，確認拆分後仍生效）。
- [ ] IaC 靜態驗證（template 結構）：新增 `tests/test_template_schedule.py`，用能解析 CloudFormation intrinsic tag（`!Ref`/`!Sub`/`!GetAtt` 等）的 YAML loader 載入 `infra/template.yaml`（例如自訂 `yaml.SafeLoader` + `add_multi_constructor("!", ...)`），斷言：
  - `SchedulerFunction` 有 `SyncSchedule` 與 `NotifySchedule` 兩個 `ScheduleV2` events，皆 `State: ENABLED`、`ScheduleExpressionTimezone: Asia/Taipei`，cron 分別為 08:00 / 10:00，`Input` 分別含 `"mode": "sync"` / `"mode": "notify"`，且各有 `RetryPolicy`（`MaximumRetryAttempts: 2`、`MaximumEventAgeInSeconds: 3600`）。
  - 不存在殘留的 `Daily` event 或 `State: DISABLED` 於 SchedulerFunction。
  - 存在 `PipelineAlarmsTopic`、`AlarmEmailSubscription`（Protocol email、Endpoint 引用 `AlarmEmail` 參數）、以及三個 `AWS::CloudWatch::Alarm`（scheduler Errors、processor Errors、scheduler Duration），三者 `AlarmActions` 皆引用 `PipelineAlarmsTopic`。
  - `Parameters` 含 `AlarmEmail`。
- [ ] 既有全測試綠：`PYTHONPATH=src uv run pytest tests/ -v`。

## Definition of done
- 全部 deliverable 打勾。
- `PYTHONPATH=src uv run pytest tests/ -v` 全綠。
- 若環境有 sam CLI：`cd infra && sam validate --lint` 通過（無則以上面的 template 結構測試 + `make build` 於 Phase 3 兜底，於 QA report 標明採用哪種）。
- 確認：兩個排程皆 `State: ENABLED`、mode payload 正確、RetryPolicy 就位、SNS + 三 alarm + `AlarmEmail` 參數齊備、真實 email 未進版控、PR-A 的 backend/IAM 未被破壞。
- evaluator 的 QA report 無 P0/P1。
- **PR body 與 cutover 提醒**：本 PR 是 go-live（ENABLE）。部署前必須先停本地 launchd（10:30 pipeline + 00:00 downsync）再部署，避免雙推；deploy 時需提供 `AlarmEmail` 並於部署後點 email 中的 SNS 確認連結——皆屬 Phase 3 ops，PR body 需明列此 cutover 順序。
