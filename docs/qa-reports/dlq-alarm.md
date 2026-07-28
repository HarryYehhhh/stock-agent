# QA Report: dlq-alarm

- **Contract**: 無（feature skill，直接對照需求）
- **需求**：在 `infra/template.yaml` 補 SQS DLQ alarm（`docs/runbook.md` 明訂「DLQ 有任何訊息都應通知」但缺 alarm）
- **Date**: 2026-07-28
- **Verdict**: ✅ Pass

## 需求 checklist

| 項目 | Status | Note |
|---|---|---|
| 資源 `DlqMessagesAlarm` | ✅ | `infra/template.yaml:423` |
| `AlarmName: !Sub "${AppName}-dlq-messages"` | ✅ | `infra/template.yaml:426` |
| `Namespace: AWS/SQS` / `MetricName: ApproximateNumberOfMessagesVisible` | ✅ | `infra/template.yaml:427-428` |
| `Dimensions: QueueName = !GetAtt UpdatesDLQ.QueueName` | ✅ | `infra/template.yaml:429-431`；`UpdatesDLQ` 資源存在（`infra/template.yaml:220-224`） |
| `Threshold: 1` / `GreaterThanOrEqualToThreshold` / `Statistic: Maximum` / `Period: 300` / `EvaluationPeriods: 1` / `TreatMissingData: notBreaching` | ✅ | `infra/template.yaml:432-437`，逐項比對無誤 |
| `AlarmActions` 掛 `PipelineAlarmsTopic` | ✅ | `infra/template.yaml:438`；`PipelineAlarmsTopic` 資源存在（`infra/template.yaml:360-363`） |
| 風格對齊既有三個 alarm | ✅ | 屬性順序、縮排、`AlarmActions: [!Ref ...]` 寫法皆與 `SchedulerErrorsAlarm`/`ProcessorErrorsAlarm`/`SchedulerDurationAlarm` 一致 |
| `docs/runbook.md` SQS 監控條目補一句 | ✅ | `docs/runbook.md:74` 補「已由 `earnings-agent-dlq-messages` alarm 涵蓋」，與 `AppName` 預設值 `earnings-agent` 組合後的實際 AlarmName 一致 |
| 純 IaC 變更，不需新增測試 | ✅ | 未新增測試檔，符合需求 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -q`
- 結果（沙盒外重跑確認）：452 passed, 0 failed, 0 skipped
- 附註：沙盒內執行時 `tests/test_local_integration.py` 有 13 個 `PermissionError: [Errno 1] Operation not permitted`（寫入暫存 SQLite 檔案被沙盒擋下），與本次改動無關，屬環境限制非 regression；沙盒外重跑後全數 452 顆通過，與 generator 回報的 452 passed 一致
- 既有 suite regression：✅ 無

## IaC 驗證

- `sam validate --region ap-northeast-1 --lint` → `infra/template.yaml is a valid SAM Template`（重跑確認一致）
- `AWS::SQS::Queue` 的 `!GetAtt <QueueLogicalId>.QueueName` 為合法 GetAtt attribute（CloudFormation 官方文件列出 SQS Queue 支援 `Arn`、`QueueName` 兩個 GetAtt 屬性），且模板中既有 `WebhookFunction`/`ProcessorFunction` 的 `SQSSendMessagePolicy`/`SQSPollerPolicy` 已使用 `!GetAtt UpdatesQueue.QueueName`（`infra/template.yaml:245`, `284`），用法一致、經 `sam validate --lint` 驗證通過，非新增風險

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）
無

### 🟢 Suggestions
- **tests/test_template_schedule.py**：此檔以靜態解析 `infra/template.yaml` 的方式驗證既有三個 alarm（`test_scheduler_and_processor_errors_alarms`、`test_scheduler_duration_alarm`）的屬性結構，但未涵蓋新增的 `DlqMessagesAlarm`。需求明訂本次「純 IaC 變更，不需新增 pytest 測試」，故不視為缺陷；若之後有心力，可比照既有 pattern 補一個 `test_dlq_messages_alarm` 靜態斷言（Namespace/MetricName/Dimensions/Threshold），讓四個 alarm 的測試覆蓋一致，純屬選配。

### ✅ What looks good
- diff 完全 surgical：僅 `infra/template.yaml`（+17 行，新增一個資源區塊）與 `docs/runbook.md`（改一行）兩個檔案，無無關改動
- 新 alarm 的屬性順序、縮排、`AlarmActions` 寫法與既有三個 alarm 完全一致，風格零違和
- `Dimensions.Value` 正確引用 `!GetAtt UpdatesDLQ.QueueName`（而非誤用 `!Ref` 拿到 Queue URL），符合 CloudWatch `AWS/SQS` metric 的 dimension 慣例
- runbook 補的一句話精準對應到 `!Sub "${AppName}-dlq-messages"` 展開後（`AppName` 預設 `earnings-agent`）的實際 AlarmName，文件與 IaC 保持同步

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無（可選：日後補 `DlqMessagesAlarm` 的靜態結構測試，非本次必要）
