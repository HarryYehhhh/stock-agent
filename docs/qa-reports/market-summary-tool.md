# QA Report: market-summary-tool

- **需求依據**：使用者需求（本次為 feature，無 contract）— 見 evaluator 派工訊息
- **Date**: 2026-07-25
- **Verdict**: ✅ Pass

## 需求 checklist
| 項目 | Status | Note |
|---|---|---|
| 新增工具 `get_market_summary`，內部現場呼叫 `agent.market_summary.generate_market_summary(days_back)`（不讀已存摘要） | ✅ | `src/agent/tools/market_summary_tool.py:38-42`；未呼叫 `refresh_news`（依需求字面只呼叫 `generate_market_summary`，且 DynamoDB 的 `insert_news`/`list_news_since` 是真實作，由每日排程的 sync 階段填充，非 no-op；`insert_market_summary` 才是 no-op） |
| 工具回傳值只含 summary 文字，不塞 news/indexes | ✅ | `return {"summary": result["summary"]}`；`test_get_market_summary_returns_only_summary` 明確斷言 `"news" not in out` / `"indexes" not in out` |
| 參數 `days_back` optional int，預設 1，限 1-7 | ✅ | schema `minimum: 1, maximum: 7`（LLM 端提示）；runtime 端 `market_summary._date_from()`（`src/agent/market_summary.py:93`）已 `max(1, min(7, days_back))` 做防呆 clamp，越界值不會炸也不會被 LLM 誤導出離譜結果 |
| 掛進 `tools/__init__.py` 的 `ALL_TOOL_SCHEMAS` / `TOOL_FUNCTIONS` | ✅ | 已掛載；`python -c "from agent.tools import TOOL_FUNCTIONS"` 實測 5 個工具皆在列，無 import error |
| `SYSTEM_INSTRUCTION` 補一句工具選擇策略 | ✅ | `src/agent/agent.py:34`：「詢問市場摘要、大盤整體狀況時呼叫 get_market_summary，不要用 get_quote 猜指數代號」，與既有其他工具選擇句式一致、不衝突 |
| 附測試（mock `generate_market_summary`） | ✅ | `tests/test_tools.py` 新增 3 個：回傳值只含 summary、`days_back` 正確傳遞、預設值為 1；monkeypatch 目標為 `agent.market_summary.generate_market_summary`，經延遲 import 的函式內 `from agent.market_summary import generate_market_summary` 確認生效（3 個測試皆 pass） |
| 循環 import 疑慮（tools → market_summary → agent → tools） | ✅ | 已用 `agent.tools.market_summary_tool` 內延遲 import（函式內 `from agent.market_summary import generate_market_summary`）解掉；實測 `agent.market_summary` 確實會匯入 `agent.agent`（`from agent.agent import MODEL, _client`），而 `agent.agent` 匯入 `agent.tools`，若 `tools/__init__.py` 頂層 import `market_summary_tool` 且該檔頂層又 import `agent.market_summary`，確實會形成循環；延遲到函式內解決正確且已用 `python -c "from agent.tools import TOOL_FUNCTIONS"` 驗證無誤 |

## Test results
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：222 passed / 12 errors（既有 `tests/test_local_integration.py` 因沙盒禁止 `ThreadingHTTPServer` bind localhost 埠而 error，與本次改動無關，環境限制可忽略）
- 沙盒外重跑（`dangerouslyDisableSandbox`）：**234 passed, 0 failed**，與 generator 自報一致，12 條原本 error 的 socket-bind 測試全綠
- 新增測試單獨執行：`tests/test_tools.py -k market_summary` → **3 passed**
- 失敗測試：無
- 既有 suite regression：✅ 無回歸

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）
無

### 🟢 Suggestions
- **`CLAUDE.md:30` / `CLAUDE.md:91`**：仍寫「Four tools」「4 Tools：earnings calendar、analyst estimates、stock news、quote」，本次改動後已是 5 個工具。這份文件未列入本次使用者需求範圍（surgical change 原則下不強制本次改），但下次任何人讀 CLAUDE.md 取得專案現況時會拿到過時資訊，建議下一個小改動順手更新這兩處。
- **`src/agent/tools/market_summary_tool.py` 檔名**：同目錄其餘工具檔皆無 `_tool` 後綴（`earnings.py`、`estimates.py`、`news.py`、`quote.py`），此檔用 `market_summary_tool.py`。這是為了與頂層 `agent.market_summary` 模組區隔（若命名為 `market_summary.py` 放在 `tools/` 底下，人眼讀 import 語句時較容易搞混兩個同名但路徑不同的模組），屬合理權衡，非必須修正，僅供留意。

### ✅ What looks good
- 延遲 import 的取捨判斷正確且必要，docstring 把循環 import 的成因（`tools → market_summary_tool → agent.market_summary → agent.agent → agent.tools`）寫清楚，之後有人想「優化」成頂層 import 時能立刻看懂為什麼不行。
- 工具回傳值裁切乾淨：只轉手 `result["summary"]`，不把 `generate_market_summary()` 回傳的 `news`/`indexes` 塞進 tool result，避免 token bloat，且有測試明確斷言排除這兩個 key，而不是只斷言 `summary` 存在（測試品質好，能抓到「不小心整包回傳」的回歸）。
- `days_back` 的 1-7 邊界防呆其實已經在既有的 `market_summary._date_from()` 做過 clamp（非本次新增），本次工具沒有重複造輪子再加一層驗證，也沒有漏掉——正確地信任並依賴既有的下游防呆，符合「最小實作」原則。
- `SYSTEM_INSTRUCTION` 新增句與既有「查詢即時股價或當日漲跌時呼叫 get_quote」相鄰，明確劃出 get_quote 與 get_market_summary 的使用邊界（「不要用 get_quote 猜指數代號」），對 LLM 工具選型有實際指導意義，不是空泛的一句話。
- 測試三個 case 分工清楚（回傳值裁切 / 參數透傳 / 預設值），沒有重複測同一件事，且都用 `monkeypatch.setattr` 對到正確的 attribute（`market_summary_module.generate_market_summary`），而非 mock 錯位置導致測試假綠。

## 結論
- 是否可合併：是
- 最該先處理的 1-3 件事：無阻擋項；建議之後隨手更新 `CLAUDE.md` 的工具數量描述（非本次必要）。
