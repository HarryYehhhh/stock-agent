# QA Report: watchlist-scoped-earnings

- **Contract**: 無（feature skill，直接對照使用者需求 + 程式碼品質）
- **Date**: 2026-07-28
- **Verdict**: ✅ Pass

## 需求 checklist

| 項目 | Status | Note |
|---|---|---|
| 1. `processor_handler.py` 呼叫 `run_agent` 帶 chat_id，注入 watchlist | ✅ | `processor_handler.py:154` `run_agent(text, history, chat_id=chat_id)`；`run_agent()` 新增 `chat_id: str \| None = None` 參數，有 chat_id 時查 `get_watchlist(chat_id)` 並以獨立 system message 注入（`agent.py:121-158`）。注入內容只有 symbol 清單（`、` 分隔）+ 查詢規則文字，未附帶財報資料本身，token 成本可控 |
| 2. 泛用問題只答 watchlist；明確指定公司只答該公司 | ✅ | `watchlist_context()`（`prompts.py:82-99`）明確要求：泛用問題逐 symbol 帶 `symbol` 參數呼叫 `get_earnings_calendar`（避開 25 筆上限漏抓，見 `tools/earnings.py` 的 `EARNINGS_ROW_LIMIT = 25`）；明確指定公司/ticker 時只查該公司，不連帶回覆整個追蹤清單。工具 schema 本身已支援 `symbol` 參數（`tools/earnings.py:30-46`），指令可執行 |
| 3. watchlist 為空一律阻止財報查詢（含明確指定 ticker），非財報類不受影響 | ✅ | `WATCHLIST_EMPTY_CONTEXT`（`prompts.py:73-79`）明確要求「包含指定特定公司/ticker 的財報查詢」也一律不呼叫財報工具，且「非財報類問題（get_quote、get_stock_news）不受此限制」。**指令一致性已驗證**：提示語用的是 `請使用者先用 /watch <SYMBOL>`，`processor_handler.py:111` 的路由確實是 `if text == "/watch" or text.startswith("/watch ")`（非 `/add`），兩者一致。另外 `run_agent()` 在 watchlist 為空時連 code-level 的 fast path（`try_fast_answer`，繞過 LLM 直接查詢單一 ticker 財報日期）也一併跳過（`agent.py:139-148` 的 `if symbols is None or symbols:` 判斷），避免明確指定 ticker 的查詢繞過 LLM 端的阻擋規則直接查到資料——這個邊界抓得很細，是本次實作的亮點 |
| 4. 財報資訊附完整日期（年月日）與發布時段（盤前/盤中/盤後，hour 缺時標「時段未定」） | ✅ | `SYSTEM_INSTRUCTION` 新增規則（`prompts.py:15-16`）：「每筆都要附上完整日期（年月日，例如 2026-07-29）與發布時段（盤前/盤中/盤後）；hour 欄位缺漏時標示「時段未定」，不要省略或猜測」，且 bmo/amc/dmh 代碼對照已在既有規則中（`prompts.py:14`）。此為 prompt 層級指令（LLM 端落實），與本專案既有的 EPS/營收格式化規則同屬 prompt-driven 風格，做法一致 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：449 passed，13 個 `test_local_integration.py` 的 `ThreadingHTTPServer` socket bind `PermissionError`（沙盒網路限制導致，與本次改動無關）
- 停用沙盒重跑：**462 passed**，0 failed，0 skipped
- 新增/異動測試：
  - `tests/test_watchlist_scope.py`（新檔，9 case）：`watchlist_context()` / `WATCHLIST_EMPTY_CONTEXT` 純函式行為、`SYSTEM_INSTRUCTION` 格式規則、`run_agent()` 注入邏輯（有/無 chat_id、watchlist 為空/非空、跨 chat_id 隔離）、fast path 在空/非空 watchlist 下的行為差異
  - `tests/test_processor_handler.py`（+1 case）：`test_run_agent_called_with_chat_id` 驗證 `processor_handler` 實際把 chat_id 傳進 `run_agent`
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）
無

### 🟢 Suggestions

- **`src/agent/agent.py:139-148`**：watchlist 為空時阻擋財報查詢完全依賴 LLM 遵循 `WATCHLIST_EMPTY_CONTEXT` 這條 system prompt 規則，沒有 code-level 的 tool-call 攔截（例如 LLM 若不遵守指令仍可呼叫 `get_earnings_calendar`）。這與本專案既有的「新聞篩選只限追蹤清單」等規則同屬 prompt-driven 設計，非本次改動引入的新模式，且已有測試驗證正常情境下 LLM 確實遵循指令（`test_run_agent_empty_watchlist_injects_block_rule`）。若未來要做到 100% 保證，需要在 `_run_tool` 層加白名單檢查，但這會是較大的架構改動，不建議在本次 surgical 範圍內處理，僅記錄供未來參考。
- **已知限制（generator 已聲明）**：`chat_cli.py`、`scheduler_handler.py` 呼叫 `run_agent()` 未帶 chat_id，不受 watchlist scoping 影響。`chat_cli.py` 是本地除錯工具、`scheduler_handler.py` 是內部逐公司摘要生成（非使用者對話），均不在「使用者透過 Telegram 對話查詢財報」的需求範圍內，合理排除。

### ✅ What looks good

- 改動精準對應需求，diff 集中在 4 個檔案（`agent.py` +26/-8、`processor_handler.py` 1 行、`prompts.py` +31、`test_processor_handler.py` +10）加一個新測試檔，無多餘重構或無關格式調整
- `agent.py:139` 的 `if symbols is None or symbols:` 這個判斷把「未提供 chat_id（symbols=None，維持原行為）」「watchlist 非空（symbols 為非空 list）」兩種要跑 fast path 的情境合併成一個條件，同時把「watchlist 為空（symbols=[]）」正確排除，寫法精簡且有把邊界情境想清楚（見 docstring 說明 fast path 為何必須在此處被跳過）
- `watchlist_context()` 對「注入內容精簡」的需求落實到位：非空 watchlist 只放 symbol 清單本身，沒有夾帶多餘欄位（測試 `test_watchlist_context_non_empty_lists_symbols_only` 直接斷言 `epsEstimate` 不在內容中）
- 指令提示（`/watch <SYMBOL>`）與 `processor_handler.py` 實際 command routing 逐字核對一致，這正是本次驗收要求的重點項目，未發現落差
- 測試涵蓋 happy path（有/無 chat_id）、edge case（跨 chat_id 隔離、空/非空 watchlist）、以及與既有 fast path 機制的互動（這是本次改動裡最容易出錯、也最容易被忽略的邊界），覆蓋率對應需求四點皆有對應 case

## 結論

- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞項目；🟢 建議項目僅供未來架構演進參考，不影響本次合併
