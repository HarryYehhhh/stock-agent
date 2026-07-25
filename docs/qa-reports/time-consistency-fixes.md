# QA Report: time-consistency-fixes

- **驗收模式**: feature（無 contract，對照使用者需求 + 程式碼品質）
- **Date**: 2026-07-25
- **Verdict**: ✅ Pass

## 需求 checklist

| 需求 | Status | Note |
|---|---|---|
| 1a. schema description + `SYSTEM_INSTRUCTION` 教 LLM days_back 上限 7、本月也傳 7 並說明 | ✅ | `market_summary_tool.py` description 補「no data beyond 7 days back … also pass 7 (the maximum) and note … only covers the most recent 7 days」；`prompts.py` `SYSTEM_INSTRUCTION` 補「days_back 上限為 7」「本月…傳 days_back=7…僅涵蓋最近 7 天」，字面完整 |
| 1b. `run_agent` 捕捉 `tool_use_failed` 400，塞回 messages 讓模型重試（計入 `MAX_TOOL_LOOPS`），非 `tool_use_failed` 仍往外拋 | ✅ | `agent.py` 用 `try/except BadRequestError`，`error_code != "tool_use_failed"` 時 `raise`；`tool_use_failed` 時把 sanitize 過的錯誤訊息以 `role: user` 塞回 `messages` 後 `continue`，仍在同一個 `for _ in range(MAX_TOOL_LOOPS)` 迴圈內計次，耗盡後回覆既有的「推理超過最大迴圈次數」文案 |
| 2. days_back>1 摘要標註「當日」，不得表述成期間變化 | ✅ | `prompts.py` `summary_system_prompt()` >1 分支 `coverage_note` 補「大盤指數收盤數據是當日漲跌幅…提到指數數字時須標註為「當日」，不得表述成整段期間的變化」；`market_summary.py` >1 分支 `coverage_clause` 同步補相同語句，且 `index_label` 從 `[大盤收盤]` 改為 `[大盤收盤（當日漲跌）]` |
| 硬性限制：days_back=1 路徑 byte-for-byte 凍結測試維持通過且測試本身未被修改 | ✅ | 見下方獨立比對 |

## 凍結測試獨立驗證

- `git diff main -- tests/test_market_summary.py tests/test_prompts.py` 顯示 `test_generate_summary_days_back_1_user_content_is_byte_for_byte_identical_to_pre_sprint_baseline`（test_market_summary.py）與 `test_summary_system_prompt_days_back_1_is_byte_for_byte_identical_to_pre_sprint_baseline`（test_prompts.py）**未出現在 diff 中**——generator 沒有動這兩條測試本身。
- `market_summary.py` days_back<=1 分支：`coverage_clause = ""`、`index_label = "[大盤收盤]"`，與凍結前完全一致；`summary_system_prompt(1)` 沿用未變的 `if days_back <= 1` 分支（`coverage_note = ""`）。
- 兩條凍結測試單獨執行皆 PASS（見下方 Test results）。

## days_back>1「當日」標註逐項確認

- `prompts.py::summary_system_prompt(7)`（system prompt）含「當日」與「不得表述成整段期間的變化」。
- `market_summary.py::generate_market_summary(days_back=7)` 產生的 `user_content`（`messages[1]`）同時含「不得表述成整段期間的變化」與 `[大盤收盤（當日漲跌）]`。
- 對應新增測試 `test_generate_summary_multi_day_window_annotates_index_as_daily_change`、`test_summary_system_prompt_period_wording_requires_daily_index_label` 直接斷言上述字串，PASS。

## Test results

- 指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙箱內結果：292 passed，12 errors（全部是 `tests/test_local_integration.py` 的 `ThreadingHTTPServer` `socket.bind` `PermissionError: Operation not permitted`，屬沙箱網路權限限制，與本次改動無關）
- 停用沙箱重跑：**304 passed, 0 failed**（與 generator 自報一致）
- 針對性重跑 `tests/test_agent.py tests/test_market_summary.py tests/test_prompts.py tests/test_tools.py`：91 passed
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical
無。

### 🟡 Warnings
無。

### 🟢 Suggestions

- **`src/agent/agent.py:94`**：`error_code = e.body.get("error", {}).get("code") if isinstance(e.body, dict) else None` 對 `e.body` 非 dict 的情況（如 API 回傳非 JSON body）已用 `isinstance` 防護；但若 `e.body` 是 dict 且 `error` 鍵存在但值不是 dict（例如 `{"error": None}` 或 `{"error": "some string"}`），`.get("code")` 會在 `str`/`None` 上呼叫而拋 `AttributeError`，且此例外發生在 `except BadRequestError` 區塊內、非 `tool_use_failed` 分支也不會被吞掉，會以未預期的例外型別往外傳。Groq/OpenAI 相容格式下 `error` 實務上一律是 object，機率極低；且 `processor_handler.py:121-125` 對 `run_agent()` 已有外層 `except Exception` 兜底、不會讓 Lambda 整體掛掉，只是不會走到 retry 邏輯而已，不影響本次驗收判定。若要更穩健，可將該行改成 `e.body.get("error") if isinstance(e.body.get("error"), dict) else {}` 或用 `try/except (AttributeError, TypeError)` 包住取值。不強制修。
- **對話歷史污染風險已排除**：`run_agent()` 塞進 `messages` 的「工具呼叫參數不符合規範…」提示訊息只存在於函式內的暫時 `messages` list，`run_agent()` 只回傳最終 `.strip()` 過的文字；`processor_handler.py:127-128` 的 `save_message()` 呼叫存的是原始 `text`（使用者輸入）與 `reply`（最終回覆），並未把整個 `messages` 存進對話歷史，因此重試提示不會污染後續多輪對話。已確認無風險，僅記錄判定依據。

### ✅ What looks good

- `_bad_request_error()` 測試 helper 用真實 `httpx.Response` + `BadRequestError(..., response=resp, body=resp.json())` 建構錯誤物件，貼近 Groq SDK 實際拋出的例外形狀，比純 mock 屬性更可信。
- 三條新增測試（重試成功、非 `tool_use_failed` 仍拋出、耗盡 `MAX_TOOL_LOOPS`）都有斷言 `call_count` 與訊息內容（`last_call_messages` 含錯誤字串），不是只斷言回傳值，測試有效抓得住行為。
- `_sanitize_error()` 沿用既有的 token 遮罩慣例（與 `_run_tool()` 既有用法一致），錯誤訊息塞回 `messages` 前有做同樣的處理，維持既有安全慣例的一致性。
- days_back<=1 / >1 兩分支的字面隔離做得乾淨：>1 分支的新增語句沒有洩漏到 <=1 分支，避免重蹈前一個 sprint（`summary-timewindow`）round 1 的「新增句寫死在 template、不受條件化」問題。

## 結論

- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞項；上述 🟢 的 `e.body` 邊界情況為選配強化，可留待下次順手處理或不處理。
