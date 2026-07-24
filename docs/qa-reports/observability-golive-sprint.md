# QA Report: observability-golive-sprint（PR-B，go-live）

- **Contract**: docs/contracts/observability-golive-sprint.md
- **Date**: 2026-07-24
- **Verdict**: ✅ Pass

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| D1 handler mode 分流（`scheduler_handler.py`） | ✅ | `handler()` 依 `event.get("mode")` 分流 `sync`/`notify`，未知/缺 mode 在跑任何階段前 `raise ValueError`；`_run_sync()`/`_run_notify()` 各兩階段獨立 try/except、容錯後 re-raise；`_run()` 保留供 `_run_notify()` 重用與本地 `__main__`；import 補 `refresh_news`；模組 docstring 已更新說明 mode 分流與 fail-fast |
| D2 `push_daily_summary` 支援跳過 refresh（`market_summary.py`） | ✅ | 新增 `refresh: bool = True`，`refresh=False` 跳過 `refresh_news`；推播迴圈補 `summary_notified`/`mark_summary_notified` 冪等 guard，send 失敗不 mark、不中斷其他訂閱者 |
| D3 摘要冪等 guard（`db/backend.py`） | ✅ | Protocol 新增兩方法 + docstring 註記複用表；`DynamoDBBackend` 用 `notify_key=f"market-summary#{summary_date}"` conditional put + 吞 `ConditionalCheckFailedException`；`LocalSQLiteBackend` 用 sentinel `symbol="__market_summary__"` + `INSERT OR IGNORE`；未新增表/IAM |
| D4 IaC 排程拆分 + ENABLE + RetryPolicy | ✅ | 舊 `Daily` event（UTC cron(0 0 * * ? *)、`DISABLED`）已完全刪除；`SyncSchedule`(08:00)/`NotifySchedule`(10:00) 逐字對照 contract：`Asia/Taipei`、`State: ENABLED`、`Input`、`RetryPolicy`(2/3600s)；`NewsTable` IAM/env 與其餘 scheduler policies 未動 |
| D5 IaC SNS + Parameter + Alarms | ✅ | `AlarmEmail` Parameter（無 Default）、`PipelineAlarmsTopic`、`AlarmEmailSubscription`（Endpoint 引用 `AlarmEmail`）、三個 Alarm（Scheduler Errors≥1 Sum/300s、Processor Errors≥1 Sum/300s、Scheduler Duration≥250000ms Maximum/300s，皆 `TreatMissingData: notBreaching`、`AlarmActions` 指向同一 Topic）逐字對照 contract；`samconfig.toml` 僅加註解示範 override 寫法，未填真實信箱 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：212 passed / 12 errors（既有 `finnhub_stub` socket-bind 沙盒限制，含本次改動的 `test_local_integration.py:127` 那條也在其中，屬環境限制非邏輯問題）
- **非沙盒（`dangerouslyDisableSandbox`）重跑**：**224 passed, 0 failed**（12 條 socket-bind 測試全綠，含改動過的 `test_processor_subscribe_and_scheduler_share_local_store`），與 generator 自報一致
- 失敗測試：無
- 既有 suite regression：✅ 無
- `cd infra && sam validate --lint`：✅ `infra/template.yaml is a valid SAM Template`

## Findings

### 🔴 Critical（Must Fix）

無。

### 🟡 Warnings（Should Fix）

無。

### 🟢 Suggestions

- **`src/agent/db/backend.py`**：`summary_notified`/`mark_summary_notified` 複用 `earnings_notifications` 表 + sentinel key（DynamoDB `notify_key=f"market-summary#{date}"`、sqlite `symbol="__market_summary__"`）在概念上略微 overload 表名語意；ADR-0007 §「Consequences」已明確記載此 trade-off 並說明未來若通知類型變多可重新命名，屬已知且有意識的決策，不需現在改。

### ✅ What looks good

- **go-live 正確性逐字核對**：`infra/template.yaml` 的 `SyncSchedule`/`NotifySchedule` cron、`ScheduleExpressionTimezone: Asia/Taipei`、`Input`、`RetryPolicy`、`State: ENABLED` 與 contract D4 pseudocode 逐字一致；舊 `Daily` / `DISABLED` / `UTC` 完全無殘留（`test_template_schedule.py::test_no_disabled_state_remains_on_scheduler_function` 與 `test_scheduler_events_split_into_sync_and_notify_both_enabled` 雙重覆蓋）。
- **冪等 guard 交叉驗證扎實**：`test_dynamodb_backend.py::test_dynamodb_summary_notified_does_not_collide_with_earnings_notifications` 與 `test_storage_backend.py::test_summary_notified_does_not_collide_with_earnings_notifications` 明確驗證「真實財報通知不會讓摘要 sentinel 誤判為已通知、反之亦然」，直接對應特別查核點 #2 的碰撞風險；`test_push_daily_summary_send_failure_is_not_marked_notified_and_retries` 驗證「送失敗不 mark、可安全重試、不影響其他已成功訂閱者」的語意。
- **stale 標註在拆分後仍生效有專屬回歸測試**：`test_scheduler.py::test_handler_notify_mode_stale_banner_still_applies_after_split` 直接走 `handler({"mode": "notify"}, CTX)` 全路徑，斷言 `_stale_banner` 仍出現且 `push_daily_summary` 收到 `refresh=False`，比僅測 `_run()` 更貼近 contract 要求的「notify path 下 stale 仍生效」。
- **本地開發路徑零破壞**：`Makefile` 的 `notify-earnings`/`sync-earnings`/`daily-summary` target 逐行核對未變動；`scheduler_handler.py`/`market_summary.py` 的 `__main__` 區塊逐行核對未變動（`push_daily_summary()` 呼叫預設 `refresh=True`）。
- **`test_local_integration.py` 修改未弱化測試意圖**：`{"mode": "notify"}` 取代原單次 `handler()` 呼叫後，測試仍完整驗證 sync（`sync_earnings`）→ notify（`handler` 全路徑含 push）共用同一本地 sqlite store 的端到端串接，斷言最終推播內容不變；修改幅度（5 行，僅呼叫方式 + 註解）與 D1 拆分直接對應，非規避測試。
- **手術式改動**：`git diff --stat` 顯示僅 12 個檔案改動，每個改動逐行可對應 contract 的某條 Deliverable 或其必然連帶（如 `db/backend.py` 純新增兩方法、`market_summary.py` 僅新增 `refresh` 參數與 guard 邏輯、`infra/template.yaml` 僅新增/替換排程與 alarm 區塊），無 orphan import 或無關重構。
- **無恆真斷言或假陽性**：抽查的容錯測試（`test_handler_*_stage_failure_does_not_block_other_stage_but_raises`）雖用 `pytest.raises(Exception)` 略寬鬆，但同時具體斷言了各階段呼叫次數，仍能捕捉「未 re-raise」或「另一階段被跳過」的迴歸，非空判斷。

## 結論

- 是否可合併：**是**
- 最該先處理的 1–3 件事：無阻擋事項。cutover 部署順序（先停本地 launchd 再部署 ENABLE、部署時提供 `AlarmEmail`、部署後確認 SNS email subscription）已明確記載於 contract DoD 與 `docs/TASKS.md`，屬 Phase 3 ops 範圍，非本次 evaluator 職責，但建議 PR body 依 contract 要求明列此順序。
