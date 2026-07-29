# QA Report: trace-playbook-hits-test

- **Contract**: 無獨立 contract；本次為補測試覆蓋缺口，依據 docs/qa-reports/request-trace-and-corrections-sprint.md 的 🟡 Warning（`test_trace_instrumentation.py` 缺一個 completed+工具呼叫+命中 playbook 的組合測試）與其引用的 contract 條目（`docs/contracts/request-trace-and-corrections-sprint.md` 第 89 行「Tests required › 欄位齊備」）
- **Date**: 2026-07-29
- **Verdict**: ✅ Pass

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| 補一個 `completed` + 有工具呼叫 + 命中 playbook 的組合測試，斷言 `trace["playbook_hits"]` 含 source + score | ✅ | `tests/test_trace_instrumentation.py:136-162` 新增 `test_completed_outcome_with_tool_call_and_playbook_hit_saves_trace`，query 用「本週有什麼公司發布財報」（既有 `test_zero_invasion_...` 測試已驗證此 query 會注入 `earnings#財報行事曆時間查詢` playbook），mock 一次 `get_earnings_calendar` 工具呼叫 + final text，斷言 `outcome=="completed"`、`tools` 含該筆呼叫、`playbook_hits[0]["source"]=="earnings#財報行事曆時間查詢"` 且 `score >= MIN_SCORE` |
| 純補測試，不得改 production code | ✅ | `git diff --stat` 僅 `tests/test_trace_instrumentation.py` 一檔，+29/-0；`src/` 底下無任何改動 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -q`（沙盒內 `test_local_integration.py` 因 `ThreadingHTTPServer` socket bind 被 sandbox 網路限制擋下，需停用 sandbox 重跑——與本次改動無關的既有現象，QA 過程亦以此方式重現）
- 沙盒內結果：519 passed，13 個 `test_local_integration.py` PermissionError（既有已知沙盒限制，非本次新增測試、非 regression）
- 停用沙盒重跑結果：**532 passed**，0 failed，0 skipped（與 generator 自報 531+1 一致）
- 失敗測試：無
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）
無

### 🟢 Suggestions
無

### ✅ What looks good

- **改動範圍精準**：`git diff` 只碰了一個測試檔案的 29 行新增，無任何 production code 變動，符合「純補測試」要求。
- **斷言非恆真，已用獨立腳本交叉驗證**：以 `python -c "from agent.agent import _retrieve_playbook_hits, MIN_SCORE; print(_retrieve_playbook_hits('本週有什麼公司發布財報'))"` 直接呼叫真實檢索函式（不經 mock），得到 `('earnings#財報行事曆時間查詢', score=10.7158...)`，遠高於 `MIN_SCORE=3.5`，確認新測試命中的是真實計算路徑而非巧合或恆真斷言。
- **query 選擇合理且有前例可查證**：選用的「本週有什麼公司發布財報」與既有 `test_zero_invasion_messages_and_reply_unaffected_by_trace`（同檔案 :263-273）用的是同一句查詢，該既有測試已獨立驗證此 query 會注入 `earnings#財報行事曆時間查詢` 的 playbook context——新測試延用同一句是合理、可交叉核對的選擇，而非另尋一個未經驗證的例子。
- **同時覆蓋 fast_path 排除**：查詢句中無股票代號，`fast_path.py` 的 ticker-adjacency 規則不會觸發，因此測試確實走到 `run_agent()` 的 LLM 工具呼叫迴圈（mock 的 `side_effect` 兩段式回應被實際消費），而非誤入 fast_path 短路——測試通過本身即是此路徑正確被走到的證據。
- **命名與風格與既有測試一致**：函式命名、docstring 引用 contract 條目出處、mock 手法（`monkeypatch.setitem(agent_module.TOOL_FUNCTIONS, ...)`、`_tool_response`/`_text_response` helper）皆沿用檔案既有慣例，未引入新 pattern。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無，本次補測試已完整回應原 QA report 的 🟡 項目，沒有進一步需要處理的事項。
