# QA Report: token-budget-hardening

- **Contract**: 無（feature без sprint contract；驗收依據為主 Claude 下達的三項需求，見下）
- **Date**: 2026-07-27
- **Verdict**: ✅ Pass

## 需求 checklist
| 項目 | Status | Note |
|---|---|---|
| 1. chat_cli 加 INFO logging | ✅ | `main()` 開頭 `logging.basicConfig(level=logging.INFO, ...)`，測試以 monkeypatch 驗證呼叫參數 |
| 2. `get_earnings_calendar` 欄位白名單 + 上限 50 | ✅ | `EARNINGS_FIELDS`（9 欄）+ `EARNINGS_ROW_LIMIT=50`；先切片再過濾欄位；不破壞 scheduler/快取（見下方獨立驗證） |
| 3. `run_agent` 接住 `APIStatusError`（413/429）不 crash | ✅ | `except APIStatusError` 置於 `except BadRequestError` 之後，回友善訊息；不重試 |
| Surgical change only | ✅ | 只動 `chat_cli.py` / `earnings.py` / `agent.py` 三個來源檔 + 對應三個測試檔，無無關改動 |

## Test results
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙箱內結果：361 passed / 13 errors（`test_local_integration.py` 的 `ThreadingHTTPServer.bind` 被 sandbox 擋 `PermissionError: Operation not permitted`，屬既有本機 socket 限制、與本次改動無關）
- 停用沙箱重跑：**374 passed / 0 failed**（與 generator 自報一致）
- 既有 suite regression：✅ 無（含 400 tool_use_failed 既有測試 `test_tool_use_failed_400_exhausts_max_loops` 等全數維持綠燈）

## 重點審查（獨立讀碼驗證，非僅信 generator 摘要）

### 1. except 順序正確性（非 tool_use_failed 的 400 是否仍會 raise）
獨立確認 groq SDK 例外階層：`BadRequestError` 是 `APIStatusError` 的子類別（`groq.BadRequestError.__mro__` = `(BadRequestError, APIStatusError, APIError, GroqError, Exception, ...)`）。`agent.py` 中 `except BadRequestError` 先於 `except APIStatusError`，符合 Python「更特定的例外類別要放前面」規則：
- `run_agent` 內非 `tool_use_failed` 的 400 會在 `except BadRequestError` 區塊內 `raise`（`agent.py:161`），這是**在 except 子句內重新拋出**，Python 語意上會直接向外傳播、不會被同一個 `try` 底下的後續 `except APIStatusError` 攔截（sibling except 只作用於 try 區塊本身拋出的例外，不會攔截 except 區塊內的 re-raise）。以獨立 `python -c` 重現此語意（三層 try/except/raise 行為），確認邏輯正確。
- 另外用 `Groq._make_status_error` 原始碼確認 413 沒有專屬子類別（落到最後的 `return APIStatusError(...)`），429 對應到 `RateLimitError`（`APIStatusError` 子類、非 `BadRequestError` 子類）。因此兩者都只會被第二個 `except APIStatusError` 攔截，不會誤觸第一個 `except BadRequestError` 的 `error_code` 判斷邏輯。

### 2. 「瘦身資料同時用於 upsert 與回傳」風險點
逐一讀碼確認兩個 backend 的 `upsert_earnings_calendar` 實際只用到欄位：
- `LocalSQLiteBackend.upsert_earnings_calendar`（`db/backend.py:516-548`）：只讀 `item["symbol"]`、`item["date"]`、`item.get("hour")`、`item.get("epsEstimate")`、`item.get("revenueEstimate")`
- `DynamoDBBackend.upsert_earnings_calendar`（`db/backend.py:1039-1058`）：同樣只讀這 5 欄

兩者皆是 `EARNINGS_FIELDS`（9 欄）的子集，白名單瘦身後 upsert 仍可正常寫入。

`scheduler_handler.sync_earnings()`（`scheduler_handler.py:49-67`）呼叫 `get_earnings_calendar(...)` 純粹是為了觸發內部的 `upsert_earnings_calendar` side effect（**不使用回傳值**，只用 try/except 判斷是否失敗），因此回傳值瘦身與否對 sync 流程完全無影響。`_notify_local`（`scheduler_handler.py:124-159`）讀的是 `backend.get_watchlist_earnings_for_date()`，資料來源是 DB 裡已寫入的欄位（`extended_hour`/`eps_estimate`/`revenue_estimate`），不是 `get_earnings_calendar` 的回傳值本身。`local_server.py` 的 `/earnings` debug endpoint 只是把回傳值原樣透傳給呼叫端，屬本機除錯端點，非快取/scheduler 消費者，不受影響。

結論：generator 的設計決定（瘦身資料同時用於 upsert 與回傳）成立，無風險。

### 3. 白名單欄位是否涵蓋 playbook 要求
讀 `knowledge/playbooks/earnings.md`「回覆要求」：每筆財報須同時標示日期與發布時段（對應 Finnhub `hour` 欄位 bmo/amc/dmh）。`EARNINGS_FIELDS` 含 `date`、`hour`，符合。另 playbook 本身雖未在「回覆要求」段落強制要求 EPS/營收預期，但白名單仍保留 `epsEstimate`/`revenueEstimate`（供「特定個股財報與分析師預期查詢」情境使用），涵蓋充分、無遺漏。

### 4. 413/429 測試是否真的走 APIStatusError 路徑
確認 `tests/test_agent.py` 新增的 `_request_too_large_error()` / `_rate_limit_error()` 是用真實 `httpx.Response`（含 status_code 413/429）建構真正的 `groq.APIStatusError` / `groq.RateLimitError` 例外物件（不是用 `MagicMock` 偽裝屬性），並透過 `mock_client.chat.completions.create.side_effect = ...` 讓 Groq client 呼叫時真正拋出該例外物件，走的是 `run_agent()` 真實的 `except APIStatusError` 分支，非 mock 過頭繞過邏輯。已重跑驗證兩測試皆綠燈。

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）
無

### 🟢 Suggestions
- `agent.py:167` 的友善訊息「這個問題需要的資料量超過目前額度限制」對 429（純請求數超限，與單次請求資料量無關）用詞略不精準，可考慮依 `e.status_code` 區分文案（413 講資料量、429 講頻率）。非阻塞，現有措辭仍可讓使用者理解「稍後再試/拆小問題」的行動指引。
- `EARNINGS_FIELDS` 上方註解提到「epsActual/revenueActual/quarter/year 是為了 LLM 對歷史財報的查詢保留，但目前沒有任何 cache/scheduler consumer 讀取」——這是誠實且有用的文件化，未來若要進一步瘦身可參考此註解，不需現在處理。

### ✅ What looks good
- except 順序與 groq SDK 例外階層的對應關係正確且經獨立驗證（非僅信任摘要）
- 欄位白名單決定有明確依據（讀了兩個 backend 的實際欄位使用），且測試 `test_earnings_calendar_upserts_thinned_data` 專門鎖定這個設計決定，未來若 backend 需要新欄位、測試會先破
- 413/429 測試用真實 SDK 例外物件而非過度 mock，測試信度高
- 改動範圍完全 surgical，只動三個來源檔 + 對應測試，無無關 refactor

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞項；🟢 建議項（413/429 文案區分）可視情況於下次順手處理，非必要
