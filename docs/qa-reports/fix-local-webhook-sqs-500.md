# QA Report: fix-local-webhook-sqs-500

- **Contract**: 無正式 contract（bug fix，直接對照原始需求 + 程式碼品質驗收）
- **Date**: 2026-07-23
- **Verdict**: ✅ Pass

## 需求 checklist

| Deliverable | Status | Note |
|---|---|---|
| 先寫重現測試，確認修復前 500 | ✅ | 用 `git stash push -- src/agent/local_server.py` 只還原修復本身，重跑新增的 `test_rest_webhook_first_delivery_succeeds_without_real_sqs_queue`，實測 `assert 500 == 200` 失敗，與 root cause（`webhook_handler.handler()` 呼叫真實 `_sqs().send_message()`，本地未建 queue → `QueueDoesNotExist`）完全吻合 |
| 修復：本地 `/webhook` 首次投遞即 200 | ✅ | `_no_op_sqs()` context manager 在呼叫 `webhook_handler(...)` 期間把 `webhook_handler_module._sqs` 換成回傳 no-op `send_message` 的假物件；`git stash pop` 還原修復後重跑同一測試通過 |
| (a) Lambda 生產行為不變 | ✅ | `git diff -- src/agent/webhook_handler.py` 為空，零改動；`tests/test_webhook.py` 5 案例（含 `test_valid_request_enqueues_to_sqs`）全過，Lambda 路徑仍真的呼叫 SQS |
| (b) 本地不可指向真實部署 queue | ✅ | 新測試刻意不建 queue、`UPDATES_QUEUE_URL` 指向假 ARN；`_no_op_sqs()` 讓 `_sqs()` 根本不會被呼叫，local dev 不會對任何真實/moto queue 發出請求 |
| 手術式修改 | ✅ | `local_server.py` diff 僅新增 `_no_op_sqs()` context manager（15 行）+ 呼叫段包一層 `with`，未動其他 endpoint |
| `make test` 全綠 | ✅ | 見下方 Test results |

## 測試有效性複查（依要求重點檢查）

- **monkeypatch 是否真的生效（而非早綁定失效）**：`webhook_handler.py:56` 的 `_sqs().send_message(...)` 是在 `handler()` 函式*呼叫時*才對 `_sqs` 做 module-global 查找，而非 import 時期綁定。`local_server.py` 用 `from agent import webhook_handler as webhook_handler_module` 拿到的是同一個 module 物件，`_no_op_sqs()` 直接改寫 `webhook_handler_module._sqs`（即改寫 `agent.webhook_handler.__dict__["_sqs"]`），與 `handler()` 內部查找的是同一個 dict，故 patch 確實生效。用 `git stash` 拿掉修復後重跑新測試得到 `500`（見上表），實測驗證了因果關係，非僅憑程式碼推論。
- **restore 邏輯**：`try/finally` 確保即使 `webhook_handler(...)` 拋例外，`webhook_handler_module._sqs` 仍會還原成原本被 `@lru_cache` 包裝的函式物件，不會有殘留污染。
- **新測試是否真的模擬「無 queue」情境**：`test_rest_webhook_first_delivery_succeeds_without_real_sqs_queue` 只用 `local_store`（不用會建立真實 moto queue 的 `aws` fixture），手動包一層 `mock_aws()` 且全程未呼叫 `create_queue`，`UPDATES_QUEUE_URL` 指向假 ARN。若 `_no_op_sqs()` 失效，`_sqs()` 會在 `mock_aws()` 攔截下對不存在的 queue 發 `send_message`，得到確定性的 `QueueDoesNotExist`（而非因假 AWS 憑證連到真網路而產生的不確定行為）；已用 `git stash` 實測復現此路徑，確認測試設計正確。
- **200 是否代表整條鏈路真的跑完（而非被吞掉的假陽性）**：`local_server.py:66-70` 的 `_process_update` 呼叫包在 `try/except Exception: logging.exception(...)` 內，單看 `status_code == 200` 無法排除 `_process_update` 中途失敗被吞掉。但測試同時 `monkeypatch.setattr(processor_handler, "send_message", fake_send)` 並 `fake_send.assert_called_once()`；追蹤 `processor_handler.py`，`/start` 分支最終會呼叫 `send_message(chat_id, WELCOME)`，若鏈路中途中斷則此斷言必然失敗，故此測試不是假陽性。
- **既有 200 測試（`test_rest_webhook_accepts_valid_secret`）為何沒抓到這個 bug**：它用 `(aws, local_store, monkeypatch)` 組合，`aws` fixture 會先建立一個真實 moto SQS queue 並把 `UPDATES_QUEUE_URL` 指過去，恰好掩蓋了「本地無 queue」這個情境——這正是本次新增獨立測試（不借用 `aws` fixture）的必要性所在，而非重複覆蓋。

## 程式碼品質

- `_no_op_sqs()` 的 docstring 說明了「為什麼要 swap」，但沒有明確記錄「為什麼這個 swap 在併發下是安全的」這個隱含前提（見下方 🟡）。
- Swap 範圍精準：只替換 `webhook_handler_module._sqs`，未動 `webhook_handler.py` 本身，也未動 `claim_update_id`/dedup 等其他路徑；`grep _sqs` 確認 `_sqs` 只在 `webhook_handler.py` 內被使用，patch 不會外溢到其他模組。
- 命名、風格與既有 `local_server.py`（模組層級常數 `_LOCAL_LAMBDA_CONTEXT`、`contextlib.contextmanager` 用法）一致，無明顯 over-engineering。

### 併發安全性評估（依要求重點檢查）

`_no_op_sqs()` 修改的是 module-level 全域狀態（`webhook_handler_module._sqs`），理論上在多個並發請求同時進出 patch 窗口時會互相干擾。實際評估如下：

- `/webhook` route 是 `async def`，未被宣告成 sync def，因此 Starlette **不會**把它丟進 threadpool 執行，而是直接跑在 event loop 上。
- `with _no_op_sqs(): result = webhook_handler(event, _LOCAL_LAMBDA_CONTEXT)` 這段窗口內完全沒有 `await`：`webhook_handler.handler()`、`claim_update_id()`（DynamoDB/SQLite 皆為同步呼叫）都是純同步程式碼。Python asyncio 的協作式排程只在 `await` 點才會讓出控制權給其他 coroutine，所以在單一 event loop thread 內，這段 patch→call→restore 對其他並發請求而言是原子的。
- `make dev` 用 `uvicorn agent.local_server:app --reload --port 8080`（`Makefile:14`），未帶 `--workers`，預設單一 worker/單一 process/單一 event loop thread，符合上述前提。

**結論**：在目前唯一的實際使用方式（`make dev`）下，這個 monkeypatch 是安全的。但這個安全性完全依賴「窗口內無 await」+「單一 worker」兩個隱含前提，程式碼中未留下任何註解或測試守護；未來若有人在 `with _no_op_sqs()` 區塊內加入 `await`（例如把某個呼叫改成 async），或執行環境改成多 worker/多 thread，會靜默破壞這個假設且不會被任何測試發現。由於這只影響本地開發工具（不影響 Lambda 生產路徑），嚴重度不到 Critical，列為 🟡。

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：130 passed, 12 errors（皆為既有 `tests/test_local_integration.py` 的 `finnhub_stub`/`ThreadingHTTPServer` socket bind 沙盒限制，`PermissionError: [Errno 1] Operation not permitted`，與本次改動無關）
- 非沙盒重跑（`dangerouslyDisableSandbox`）：**142 passed, 0 failed**，與 generator 回報數字一致
- 針對性重跑：`pytest tests/test_local_integration.py -v -k webhook` → 4 passed；`pytest tests/test_webhook.py -v`（Lambda 路徑）→ 5 passed
- Mutation 驗證：`git stash` 只還原 `local_server.py` 後重跑新增的回歸測試 → 確實重現 `500`（`assert 500 == 200`），還原修復後恢復 `200`，證明測試真的鎖住這個 bug
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
- **`src/agent/local_server.py:40-54`**：`_no_op_sqs()` 的併發安全性完全依賴「patch 窗口內無 `await`」與「`make dev` 為單一 uvicorn worker」兩個未在程式碼中明文記錄的隱含前提。目前條件下安全（已驗證），但沒有任何註解或測試守護這個前提，未來改動容易靜默破壞它。
  建議修法：在 `_no_op_sqs()` docstring 補一句話說明這個前提（例如「Safe only because this window contains no `await` and `make dev` runs a single uvicorn worker; if either changes, this global monkeypatch needs revisiting.」），不需要額外程式碼變更。

### 🟢 Suggestions
- 現有做法（monkeypatch `webhook_handler_module._sqs`）是在「不能改 `webhook_handler.py`」與「本地不能打真實 queue」兩個限制下相對簡潔的選擇；曾考慮的替代方案（例如在 `local_server.py` 內直接 `try/except` 包住呼叫，讓 `QueueDoesNotExist` 靜默失敗）需要先觸發一次真實網路呼叫才會失敗，在無網路/慢速環境下會拖慢本地開發，且行為上等同又做一次隱性猜測；目前的 no-op stub 從一開始就不發出任何呼叫，更直接也更快，值得保留現況、不需改動。

### ✅ What looks good
- 用 `git stash` 做修復前/修復後的雙向 mutation test，確實證明新增的回歸測試鎖住了這個 bug（而非碰巧通過）。
- 新測試刻意避開會建立真實 queue 的 `aws` fixture，準確點出既有 `test_rest_webhook_accepts_valid_secret` 為何沒抓到這個 bug（該測試用 `aws` fixture 順帶建了 queue，掩蓋了「本地無 queue」情境），顯示對既有測試覆蓋缺口有清楚認知。
- `200` 案例透過 `fake_send.assert_called_once()` 排除「`_process_update` 例外被吞掉」的假陽性風險，測試設計嚴謹。
- `_sqs` 只在 `webhook_handler.py` 內被使用（`grep` 確認），patch 範圍精準、無外溢風險；`try/finally` 確保即使 `handler()` 拋例外也會還原。
- `webhook_handler.py` 零改動，Lambda 生產路徑行為不受影響，符合約束 (a)。

## 附註（範圍外）

工作目錄中另有 `Makefile`、`docs/deployment-flow.md`、`src/agent/market_summary.py`、`tests/test_market_summary.py` 的未提交改動，經確認屬其他既有任務（Phase 1 本地每日排程功能，對應 `docs/qa-reports/phase1-local-daily-schedule.md`）遺留的未提交變更，與本次 bug fix 無關，本報告不涵蓋其驗收。

另注意到 `docs/qa-reports/fix-local-webhook-500.md`（既有、已 Pass）處理的是同一個 `/webhook` route 上的另一個 bug（`webhook_handler(event, None)` 傳入 `None` context 導致 `@logger.inject_lambda_context` 拋 `AttributeError`）。兩次修復疊加在同一份未提交的 `local_server.py` diff 中：本報告只驗收 SQS `QueueDoesNotExist` 這一段（`_no_op_sqs()` + 呼叫段的 `with` 包裝），context fix 的部分因先前已通過 QA、且本次 diff 未再變動該部分，不重複驗收。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞項；建議（非阻塞）在 `_no_op_sqs()` docstring 補一句併發安全前提的說明。
