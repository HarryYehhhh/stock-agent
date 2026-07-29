# QA Report: ddb-retain-policy

- **Contract**: 無（直接對照需求驗收，非 sprint harness 產出）
- **Date**: 2026-07-29
- **Verdict**: ✅ Pass

## 需求 checklist

| 項目 | Status | Note |
|---|---|---|
| ConversationsTable 加 `DeletionPolicy: Retain` + `UpdateReplacePolicy: Retain` | ✅ | infra/template.yaml:41-42 |
| SubscribersTable 加兩屬性 | ✅ | infra/template.yaml:62-63 |
| WatchlistTable 加兩屬性 | ✅ | infra/template.yaml:91-92 |
| TracesTable 加兩屬性 | ✅ | infra/template.yaml:222-223 |
| CorrectionsTable 加兩屬性 | ✅ | infra/template.yaml:256-257 |
| EarningsNotificationsTable 加兩屬性 | ✅ | infra/template.yaml:135-136 |
| ProcessedUpdatesTable 保持不動 | ✅ | 無 DeletionPolicy/UpdateReplacePolicy |
| RateLimitsTable 保持不動 | ✅ | 同上 |
| EarningsCalendarTable 保持不動 | ✅ | 同上 |
| NewsTable 保持不動 | ✅ | 同上 |
| CompanySummariesTable 保持不動 | ✅ | 同上 |
| 有測試覆蓋六表有、五表無 | ✅ | `tests/test_template_schedule.py::test_retain_policy_on_tables_with_unrecoverable_data` |
| Surgical change | ✅ | `git diff` 僅動 `infra/template.yaml`（+12 行）與 `tests/test_template_schedule.py`（+29 行），無其他檔案異動、無無關 reformatting |

逐表核對 `infra/template.yaml` 全部 11 張 DynamoDB 表，確認六張目標表皆各加了 `DeletionPolicy: Retain` 與 `UpdateReplacePolicy: Retain` 兩行（位於 `Type:` 之後、`Properties:` 之前，符合 CloudFormation resource 層級屬性語法），其餘五張完全未變動。

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`（`CLAUDE.md` 指定的 `make test` 等價指令）
- 結果：533 passed（沙盒內首次執行時 `tests/test_local_integration.py` 13 個測試因 socket bind 觸發 `PermissionError: Operation not permitted`——為既有沙盒現象，與本次改動無關，沿用先前 QA report 的判斷；停用沙盒重跑後 533/533 全綠）
- 失敗測試：無
- 既有 suite regression：✅ 無
- 新增測試單獨執行：`tests/test_template_schedule.py` 8/8 passed，含新增的 `test_retain_policy_on_tables_with_unrecoverable_data`
- `sam validate --lint`（`infra/template.yaml`）：✅ valid

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
無。

### ✅ What looks good
- 改動精準對應需求：六張存放不可重建資料（對話紀錄、訂閱者、watchlist、trace、corrections、earnings 通知去重狀態）的表加上 `DeletionPolicy` + `UpdateReplacePolicy` 雙重保護，可重建的快取/衍生資料表（processed-updates dedup、rate limits、earnings calendar 快取、news 快取、company summaries 快取）維持原狀，語意正確。
- 新增測試以 allowlist/denylist 雙向斷言（六表有、五表無），能防止未來誤刪或誤加保護策略時的 regression，覆蓋率對應需求。
- Diff 完全 surgical，無無關格式調整或註解變動。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無（無 blocking 問題）
