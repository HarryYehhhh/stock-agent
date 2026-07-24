# QA Report: fix-local-webhook-500

- **Contract**: 無正式 contract（bug fix，直接對照原始需求 + 程式碼品質驗收）
- **Date**: 2026-07-23
- **Verdict**: ✅ Pass

## 需求 checklist

| Deliverable | Status | Note |
|---|---|---|
| 先寫重現測試，確認修復前 500 | ✅ | `git stash` 只還原 `local_server.py`，重跑 `test_rest_webhook_rejects_missing_secret`/`test_rest_webhook_accepts_valid_secret` 兩案例，實測皆 `AttributeError: 'NoneType' object has no attribute 'function_name'`，與 root cause（`@logger.inject_lambda_context` 存取 `context.function_name`）完全吻合 |
| 修復：本地 webhook 不再 500 | ✅ | `_LOCAL_LAMBDA_CONTEXT`（`SimpleNamespace`）補上 `build_lambda_context_model` 實際讀取的四個屬性（`function_name`/`memory_limit_in_mb`/`invoked_function_arn`/`aws_request_id`），逐一比對 `aws_lambda_powertools.logging.lambda_context.build_lambda_context_model` 原始碼確認無多餘、無缺漏屬性 |
| 無 secret → 401 | ✅ | `test_rest_webhook_rejects_missing_secret` 通過；`webhook_handler.handler` 的 secret 比對發生在 dedup/SQS 之前，測試只用 `local_store` fixture（未用 `aws`）即可斷言 401，路徑符合原始程式邏輯 |
| 正確 secret → 200 | ✅ | `test_rest_webhook_accepts_valid_secret` 通過；見下方「測試有效性複查」 |
| 不得改動 Lambda 生產路徑行為 | ✅ | `git diff -- src/agent/webhook_handler.py` 為空，零改動 |
| 手術式修改 | ✅ | `local_server.py` diff 僅 9 行新增（import + 常數 + 一處呼叫參數替換），未動其他 endpoint 或既有邏輯 |
| `make test` 全綠 | ✅ | 見下方 Test results |

## 測試有效性複查（依要求重點檢查）

- **200 案例是否真的驗到 webhook 路徑（而非靠 `_process_update` 例外被吞而誤判過）**：`local_server.py` 的 `_process_update` 呼叫包在 `try/except Exception: logging.exception(...)` 內，單看 `response.status_code == 200` 確實無法排除 `_process_update` 中途失敗。但測試同時斷言 `fake_send.assert_called_once()`（`monkeypatch.setattr(processor_handler, "send_message", fake_send)`）。若 `_process_update` 因任何例外提早中止，`send_message` 不会被呼叫，這條斷言就會失敗。追蹤 `processor_handler.py`：`/start` → `send_message(chat_id, WELCOME)` 是該分支的最終呼叫，因此 `fake_send.assert_called_once()` 等同驗證了「webhook 驗證通過 → dedup → enqueue SQS → 本地同步 `_process_update` → 命令分派 → 回覆」整條鏈路真的跑完，不是被吞掉的假陽性。
- **401 案例是否真的驗證到 secret 邏輯**：讀 `webhook_handler.py:37-40`，`secret != telegram_webhook_secret()` 是 handler 進入後第一個分支，測試請求未帶 `x-telegram-bot-api-secret-token` header，直接命中此分支回 401，且不涉及 dedup/SQS，與程式碼路徑一致。
- **`aws`/`local_store` fixture 組合是否合理**：`test_rest_webhook_accepts_valid_secret` 用 `(aws, local_store, monkeypatch)`，與既有 `test_local_webhook_duplicate_update_enqueues_once`（同檔案）採用相同 fixture 組合模式，非本次新發明的用法，風險低。

## 程式碼品質

- `SimpleNamespace` 做法簡潔，無過度設計：只放 powertools 實際需要的 4 個屬性，附上一行註解說明為什麼需要（`local_server.py:28-29`），沒有引入額外的 mock library 或自建 fake context class。
- 模組層級常數 `_LOCAL_LAMBDA_CONTEXT` 只建立一次、無副作用，符合 FastAPI 模組全域慣例（同檔案其他地方也是模組層級初始化 `app = FastAPI()`）。
- `webhook_handler.py` 完全未觸碰，確認生產 Lambda 路徑行為不受影響。
- 未見遺留的 orphan import 或改動外的程式碼被順手調整。

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：129 passed, 12 errors（皆為既有 `tests/test_local_integration.py` 的 `finnhub_stub`/`ThreadingHTTPServer` socket bind 沙盒限制，`PermissionError: [Errno 1] Operation not permitted`，與本次改動無關，非本次新增測試）
- 非沙盒重跑（`dangerouslyDisableSandbox`）：**141 passed, 0 failed**，與 generator 回報數字一致
- 針對性重跑：`pytest tests/test_local_integration.py -v -k rest_webhook` → 2 passed
- Regression：✅ 無，既有 139 條（141 減新增 2 條）測試全數通過

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
無值得一提的 nitpick。

### ✅ What looks good
- 修復前用 `git stash` 精確重現了 500 的根因（`AttributeError: 'NoneType' object has no attribute 'function_name'`），而非僅憑臆測描述，可信度高。
- 200 案例的斷言設計（`fake_send.assert_called_once()`）有效避開了「`_process_update` 例外被吞掉導致假陽性」的陷阱，是本次測試設計中最值得肯定的一點。
- 改動範圍精準對應 root cause，未牽動 `webhook_handler.py` 或其他 endpoint，符合「手術式修改」與「不得改動 Lambda 生產路徑行為」的雙重要求。

## 附註（範圍外）

工作目錄中另有 `Makefile`、`docs/deployment-flow.md`、`src/agent/market_summary.py`、`tests/test_market_summary.py` 的未提交改動，經確認為其他既有任務（`docs/qa-reports/phase1-local-daily-schedule.md` 對應的 Phase 1 每日排程功能）遺留的未提交變更，與本次 bug fix 無關，本報告不涵蓋其驗收。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞項。
