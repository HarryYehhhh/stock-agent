# QA Report: earnings-cache-fix

- **依據**：本任務無 contract，對照需求描述（production incident：使用者問「本週有什麼公司發布財報」時，`get_earnings_calendar` 因 processor 缺 DynamoDB 權限、快取寫入 `AccessDeniedException` 導致整個 tool 失敗）+ 程式碼品質
- **Branch**: feat/earnings-cache-fix
- **Worktree**: /Users/yehhungwei/stock-agent-worktrees/earnings-cache-fix
- **Date**: 2026-07-27
- **Verdict**: ✅ Pass

## 需求檢查清單

| 項目 | 狀態 | 備註 |
|---|---|---|
| 1. `infra/template.yaml` ProcessorFunction 補 `DynamoDBCrudPolicy: TableName: !Ref EarningsCalendarTable` | ✅ | `infra/template.yaml:280-283`，寫法與 `SchedulerFunction`（`infra/template.yaml:319-320`）一致 |
| 2. `get_earnings_calendar()` 的 `upsert_earnings_calendar(items)` 包 try/except，失敗只 log warning，照常回傳 items | ✅ | `src/agent/tools/earnings.py:46-49`；`except Exception` 範圍適當（僅吞快取寫入失敗，不影響已 `raise_for_status()` 成功的 Finnhub 回應），log 訊息風格與 `market_summary.py`/`telegram_client.py` 既有 `log.warning("...failed: %s", e)` 慣例一致 |
| 3. 新增重現測試：upsert 拋例外時 tool 仍回傳資料 | ✅ | `tests/test_local_integration.py:379-408` `test_earnings_calendar_returns_data_even_when_cache_write_fails`，用 `monkeypatch.setattr(get_backend(), "upsert_earnings_calendar", MagicMock(side_effect=Exception(...)))` 模擬 `AccessDeniedException`，斷言 `out == handler.payload["earningsCalendar"]`；沿用既有 `finnhub_stub`/`_use_finnhub_stub` fixture 風格，命名清楚、單一斷言鎖定行為 |
| Surgical change only | ✅ | `git diff --stat`：僅 3 個檔案，`infra/template.yaml`（+2）、`src/agent/tools/earnings.py`（+8/-1）、`tests/test_local_integration.py`（+30），無不相關改動 |
| 全測試過 | ✅ | 見下方測試結果 |
| sam validate 通過 | ✅ | 見下方 |

## 測試結果
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 結果：342 passed, 0 failed, 0 skipped
- 失敗測試：無
- 既有 suite regression：✅
- 備註：sandbox 對本機 socket bind 有限制（`finnhub_stub` fixture 用 `ThreadingHTTPServer` 綁 `127.0.0.1:0` 會 `PermissionError: Operation not permitted`），與已知環境限制一致；改用非 sandbox 模式重跑後 342/342 全數通過（含新測試 `test_earnings_calendar_returns_data_even_when_cache_write_fails`）。

## IaC 驗證
- 指令：`cd infra && sam validate --lint`
- 結果：`infra/template.yaml is a valid SAM Template`（exit code 0，無 cfn-lint 警告輸出）

## 交叉驗證：測試是否真的鎖住修復行為

閱讀 `src/agent/db/backend.py:144` 確認 `get_backend()` 有 `@lru_cache(maxsize=1)`，且 `local_store` fixture（`tests/conftest.py:192-198`）在每個測試前後呼叫 `clear_db_caches()` 清除快取——因此測試裡 `monkeypatch.setattr(get_backend(), "upsert_earnings_calendar", ...)` 拿到的 singleton 實例，與 `earnings.py` 內部呼叫 `get_backend().upsert_earnings_calendar(items)` 時拿到的是同一個物件，patch 確實會生效，不是誤判通過的偽陽性測試。

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
無新增建議；本次改動範圍精準，未發現值得記錄的 nitpick。

### ✅ What looks good
- `infra/template.yaml` 的 policy 補法完全比照 `SchedulerFunction` 既有寫法，最小化風格分歧。
- `except Exception` 的捕捉範圍恰當：只包住 `upsert_earnings_calendar(items)` 這一行，不會意外吞掉前面 `client().get(...)`/`raise_for_status()`/`r.json()` 的錯誤（那些錯誤仍應該讓 tool 失敗），符合「快取寫入 best-effort、不得吃掉已取得的 Finnhub 資料」的需求語意。
- log 訊息格式（`log = logging.getLogger(__name__)` + `log.warning("...failed: %s", e)`）與專案既有慣例（`market_summary.py`、`telegram_client.py`、`agent.py`、`estimates.py`）完全一致，沒有引入新風格。
- 新增測試沿用專案既有的 `finnhub_stub`/`_use_finnhub_stub` fixture 慣例，命名清楚、只驗證「cache 寫入失敗時 tool 仍回傳資料」這一件事，不是同義反覆測試；已用 `git stash`（generator 自報）+ 本次交叉驗證 `get_backend()` 快取語意，確認測試在修復前會失敗、修復後通過。
- Diff 範圍精準（3 個檔案，40 行新增/1 行刪除），每一行變更都可直接追溯到需求 1/2/3，沒有動到需求以外的程式碼或格式。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞項目，可直接合併。
