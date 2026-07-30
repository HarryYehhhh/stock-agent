# QA Report: tool-error-short-circuit

- **Contract**: 無 contract（feature skill，直接對照需求敘述驗收）
- **Date**: 2026-07-30
- **Verdict**: ✅ Pass

## 需求檢查表

| 需求條目 | Status | Note |
|---|---|---|
| 已知工具丟例外時決定性短路，不餵回模型、不進下一輪 Groq 呼叫 | ✅ | `src/agent/agent.py:332-338`：`if not tool_ok and tc.function.name in TOOL_FUNCTIONS` 命中即 `return finalize(...)`，不 append `role: tool` 訊息、不 `continue` 迴圈 |
| log.warning 記錄工具名與錯誤 | ✅ | `agent.py:333-337`："tool error short-circuit: tool=%s error=%s" |
| finalize() 用新 outcome `"tool_error"` | ✅ | `agent.py:338` |
| 回覆固定訊息「⚠️ 目前無法取得資料，請稍後再試。」 | ✅ | 與既有 `⚠️ ...，請稍後再試。` 風格一致（`agent.py:255/270/273`、`processor_handler.py:175`） |
| tool_trace 的 `ok: False` 紀錄保留 | ✅ | 短路前已 `tool_trace.append(...)`（`agent.py:313-321`），短路不影響此 append |
| unknown tool 情境判斷與說明 | ✅ | 用 `tc.function.name in TOOL_FUNCTIONS` 排除 unknown tool（幻覺工具名），維持原行為（餵回模型自我修正）；理由合理——unknown tool 是模型可自我修正的參數/選擇錯誤，已知工具的內部例外是系統性故障，兩者性質不同，短路範圍界定正確 |
| 測試：工具丟例外 → 固定訊息、Groq create 只叫一次、trace outcome/ok 正確 | ✅ | `tests/test_agent.py::test_tool_exception_short_circuits_without_second_groq_call`、`tests/test_trace_instrumentation.py::test_tool_error_short_circuits_and_saves_trace` |
| 測試：unknown tool 情境 | ✅ | `tests/test_trace_instrumentation.py::test_unknown_tool_error_feeds_back_to_model_and_continues`；既有 `tests/test_agent.py::test_unknown_tool_does_not_crash` 未破壞 |
| 既有測試不破壞 | ✅ | 全 suite 通過，見下方測試結果 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v --ignore=/Users/yehhungwei/stock-agent/.env --ignore=/Users/yehhungwei/stock-agent/.env.example`
- 結果：527 passed / 0 failed / 13 errors
- 失敗（error）測試：全部集中在 `tests/test_local_integration.py`（`test_earnings_calendar_fetch_writes_sqlite_with_local_finnhub_stub` 等 13 個），均為 `ThreadingHTTPServer(...).socket.bind(...)` 拋 `PermissionError: Operation not permitted`——sandbox 禁止本機 bind port 的環境限制，與本次改動的程式碼路徑（`agent.py` tool loop）無關；該檔案完全不 import `agent.agent` 模組。已核對 diff 範圍，此 13 個 error 與其他近期 QA report（如 `tool-schema-and-docs.md`、`remove-analyst-estimates.md`）記錄的既有現象一致。
- 既有 suite regression：✅ 無（527 passed 涵蓋所有非 socket-bind 測試，含改寫後的 `test_tool_exception_short_circuits_without_second_groq_call`）

## Findings

### 🔴 Critical（Must Fix）

無。

### 🟡 Warnings（Should Fix）

無。

### 🟢 Suggestions

- **`src/agent/agent.py:332`**：短路判準用 `tc.function.name in TOOL_FUNCTIONS` 而非直接檢查 `tool_result.get("tool")`（`_run_tool` 例外分支已把 `"tool": name` 放進回傳值，`agent.py:115`）。兩者在目前程式碼下結果等價，但用 `tool_result.get("tool") is not None` 可以少一次 dict 查詢、且語意更貼近「這是哪個已知工具丟出的例外」。純風格建議，不影響正確性，不要求修改。
- **多 tool_calls 情境**（同一 iteration 有多個 `tool_calls`，其中一個已知工具丟例外）：目前「遇錯即停」——短路那筆之後的 tool_calls 不會被執行、也不會有對應的 trace 紀錄。這是可接受的權衡（避免繼續耗用 Finnhub/Groq 資源在一個註定要被丟棄的 turn 上），且 `messages` 是函式內區域變數、短路後直接 return 不會再送給 Groq，不會有「assistant tool_calls 訊息數與 tool 回覆數不對」的實際外部影響。僅記錄供未來若要改成「收集完當輪所有 tool 結果再判斷」時參考。

### ✅ What looks good

- Diff 精簡且 surgical：`agent.py` 僅新增 17 行（含註解）、改 1 行既有邏輯抽出 `tool_ok` 變數；無無關改動。
- 測試斷言到位：`call_count == 1` 直接驗證「不進下一輪 Groq 呼叫」，`trace["outcome"] == "tool_error"` 與 `tool["ok"] is False` 直接驗證 trace 正確性，不是空泛的字串比對。
- unknown tool 與已知工具例外的處理邊界劃分清楚，且用 docstring/inline comment 說明了「為什麼」（model-correctable vs 系統性故障），對未來維護者友善。
- 固定訊息文案與既有其他錯誤分支（`groq_error`、`tool_use_failed_exhausted`）的用語風格一致，使用者不會感受到不一致的產品語氣。
- 既有 `test_unknown_tool_does_not_crash` 未被改動，確認 generator 沒有動到不該動的行為。

## 結論

- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞事項；上述兩點 🟢 建議純屬未來可選優化，不影響本次合併。
