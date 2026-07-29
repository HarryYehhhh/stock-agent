# QA Report: remove-analyst-estimates

- **Contract**: 無（feature-level 需求變更，無 contract 文件；對照需求描述與程式碼品質驗收）
- **Date**: 2026-07-29
- **Verdict**: ✅ Pass

## 需求 checklist

| 項目 | 狀態 | 備註 |
|---|---|---|
| 刪除 `src/agent/tools/estimates.py` | ✅ | 檔案已刪除 |
| 從 `ALL_TOOL_SCHEMAS`/`TOOL_FUNCTIONS` 移除 | ✅ | `src/agent/tools/__init__.py` 乾淨移除，無殘留 import |
| 清掉 `src/` 與 `tests/` 的引用 | ✅ | `grep -rn "analyst_estimates\|get_analyst_estimates\|ESTIMATES_TOOL" src/ tests/` 僅剩 `tests/test_tools.py` 內故意斷言「已移除」的新測試，無死碼 |
| 調整 `knowledge/` 下 playbook 與說明文件 | ✅ | `earnings-terms.md`、`analysis-frameworks.md` 改引用 `get_earnings_calendar` 的 `epsEstimate`/`revenueEstimate`（已用程式碼核對此二欄位確實存在於該 tool 回傳結構）；`playbooks/earnings.md` 第三節改名並新增「不得回覆分析師評級/目標價」禁止規則 |
| system prompt 不再提及該 tool | ✅ | `grep -n "estimate" src/agent/prompts.py` 僅剩 `COMPANY_PROMPT_TEMPLATE`（每日訂閱摘要，資料來自 earnings calendar 快取，與被移除工具無關，generator 說明合理，未改動屬正確判斷） |
| `docs/` 既有檔案不得改寫 | ✅ | `git diff HEAD --stat -- docs/` 無輸出；`docs/` 內含 "analyst" 字樣的既有檔案（CHANGELOG、specs、contracts、qa-reports）皆為歷史紀錄且未被觸碰 |
| 既有測試移除或改寫（非 skip） | ✅ | 未見任何 `@pytest.mark.skip`／`xfail`；舊測試直接刪除或替換為等價斷言 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`（依 CLAUDE.md）
- 結果（沙盒停用後重跑）：533 passed, 0 failed, 0 skipped
- 沙盒內原生跑會有 13 個 `test_local_integration.py` 因 `socket.bind` `PermissionError` 報 ERROR——這是既有沙盒環境限制（local_server 綁 port），與本次改動無關，CHANGELOG 近期多筆記錄都提到同一現象
- 既有 suite regression：✅ 無

補充驗證（超出單純跑測試）：
- `test_all_tool_schemas_have_required_fields` 與新增的 `test_analyst_estimates_tool_removed` 都會在移除失敗時真的 fail（非空轉）：手動確認若 `estimates.py` 未刪除、`__init__.py` 未清乾淨，這兩條斷言會抓到（`len(ALL_TOOL_SCHEMAS) == 5`、`names == {...5 tools...}`）
- 用 `python -m ast.parse` 對改動的三個 production 檔案做語法檢查，均通過
- 實測 BM25 playbook retrieval（`retrieve_scored` against `PLAYBOOKS_DIR`）對「TSLA 分析師怎麼看」「NVDA 目標價」「某公司分析師評級」「買入持有賣出評級」等查詢，命中的仍是 `earnings#特定個股財報日期查詢`（分數最高），因為「禁止」段落內文仍含「分析師評級」「目標價」等詞——雖然這些詞已從「觸發語」行移除，但 BM25 索引整個 section body 而非只索引觸發語行，所以引導模型「不得回覆分析師評級」的規則仍會被正確注入，playbook retrieval 沒有因這次改動而失效
- 檢查 `_run_tool()`（`src/agent/agent.py:96-115`）：對未知 tool 名稱本來就回傳 `{"error": "unknown tool '...'"}` 而不 crash，若歷史對話誘導模型再次嘗試呼叫已移除的 `get_analyst_estimates`，行為安全（回錯誤訊息給模型，不會讓 processor crash）——此為既有防護機制，非本次新增，但确認未被破壞

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- `tests/test_tools.py:53` 新增的 `test_analyst_estimates_tool_removed` 與同檔 `test_all_tool_schemas_have_required_fields`（已斷言 `names == {...5 tools...}`，隱含不含 `get_analyst_estimates`）有小幅重疊；保留兩者可讀性更好（意圖更明確），但非必要，不影響正確性
- `tests/test_agent.py` / `test_corrections_isolation.py` / `test_trace_instrumentation.py` / `test_playbook_injection.py` 中把 `get_analyst_estimates` 佔位符換成 `get_quote` 後，mock 出的 LLM 回覆文字仍寫「AAPL 分析師評級：買入 10」——這只是測試裡任意的 mock 文字（不影響斷言正確性，這些測試本來就只驗證 tool-dispatch/trace 機制而非文字內容），但讀起來語意上有點奇怪（`get_quote` 回傳卻附「分析師評級」文案）。之後若有人維護這幾個測試，可考慮把 mock 回覆文字也一併換成報價相關措辭，純可讀性提升，不阻擋合併
- 專案根目錄 `AGENTS.md`（非 `docs/` 範圍）第 31 行仍寫「Three tools: `get_earnings_calendar`、`get_analyst_estimates`、`get_stock_news`」——這份文件在本次改動前就已經嚴重過時（漏列 `get_quote`/`get_market_summary`/`search_knowledge_base`，且仍描述舊版 DynamoDB-only db 層），屬於既有技術債，非本次改動引入或應處理的範圍（需求只提及 `src/`、`tests/`、`knowledge/`、system prompt），但既然順帶發現，記錄供之後清理

### ✅ What looks good
- 移除乾淨：`git diff --stat` 顯示改動範圍精準對應需求描述的檔案清單，沒有動到其他 tool（`quote.py`/`news.py`/`market_summary_tool.py`/`knowledge_base_tool.py`/`earnings.py` 皆無 diff）
- knowledge 文件的替代方案（引導模型改用 `get_earnings_calendar` 的 `epsEstimate`/`revenueEstimate`）技術上正確——已核對 `earnings.py` 的 `EARNINGS_FIELDS` 白名單確實包含這兩個欄位，不是憑空聲稱
- playbook 新增的「禁止：不得回覆分析師評級（買入/持有/賣出分佈）或目標價等資訊——目前沒有工具能提供這類資料，不得憑空生成」直接對應本次事故根因（模型把人數當百分比講），是恰當且對症下藥的防呆
- 對 tools 數量「4→3」需求描述誤植的判斷正確：實際 codebase 原有 6 個 tool（`get_earnings_calendar`/`get_analyst_estimates`/`get_stock_news`/`get_quote`/`get_market_summary`/`search_knowledge_base`），移除後為 5 個，新測試按實際狀態斷言，未盲從錯誤的需求描述
- 未觸碰 `docs/` 既有檔案，符合「歷史紀錄不得改寫」的限制
- 沒有用 skip/xfail 迴避舊測試，符合「既有測試要移除或改寫，不能只是 skip」的要求

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻擋項；🟢 建議中的 `AGENTS.md` 過時內容可另案處理（超出本次範圍）
