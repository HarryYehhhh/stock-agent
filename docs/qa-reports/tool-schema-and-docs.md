# QA Report: tool-schema-and-docs

- **Contract**: 無正式 contract（feature skill，需求由主 Claude 於任務描述中給出）
- **Date**: 2026-07-29
- **Verdict**: ⚠️ Pass with issues

## 需求 checklist

| 項目 | Status | Note |
|---|---|---|
| 1. `search_knowledge_base` 從 tool schema 移除 `top_k`，`query` 維持必填 | ✅ | `knowledge_base_tool.py`：`input_schema.properties` 已無 `top_k`，`required` 仍為 `["query"]` |
| 1a. Python 函式簽名 `top_k=_DEFAULT_TOP_K` 保留，`_DEFAULT_TOP_K`/`_MAX_TOP_K`/`retrieve()`/clamp 邏輯不動 | ✅ | `search_knowledge_base(query: str, top_k: int = _DEFAULT_TOP_K)` 與 `k = max(0, min(top_k, _MAX_TOP_K))` 皆未變動 |
| 2. CLAUDE.md:30 / AGENTS.md:31 五工具描述更新，區分是否打 Finnhub | ⚠️ | 工具數量、名單、`search_knowledge_base` 描述皆正確；但 `get_market_summary` 的「不打 Finnhub」暗示不完全準確，見下方 🟡 |
| 3. CLAUDE.md:93 Done 清單同步 5 tools | ✅ | 已改為「5 Tools：earnings calendar、stock news、quote、market summary、knowledge base search」 |
| 4. `docs/` 既有 spec/qa-reports/CHANGELOG 不得改寫 | ✅ | `git diff` 只涉及 4 個檔案（`AGENTS.md`、`CLAUDE.md`、`knowledge_base_tool.py`、`test_tools.py`），`docs/` 目錄無異動 |
| 5. 新增測試驗證 schema 無 `top_k` | ✅ | `test_knowledge_base_tool_schema_has_no_top_k` 斷言 `properties` 不含 `top_k` 且 `required == ["query"]`，非空轉 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -q`
- 本次改動後：525 passed / 13 errors（`test_local_integration.py` 因 sandbox 擋 socket bind `PermissionError`，與本次改動無關）
- 用 `git stash` 复核改動前 baseline：524 passed / 13 errors（同樣的 13 個 sandbox 限制）
- 差異：+1 passed（新增的 `test_knowledge_base_tool_schema_has_no_top_k`），無新增失敗、無新增 error，數字與任務說明的預期（525/13，總數 538）一致
- 既有 suite regression：✅ 無
- 額外針對性驗證：`pytest tests/test_tools.py -k knowledge_base`（7 passed）、`pytest tests/test_agent.py`（18 passed，tool-use loop 含 `tool_use_failed` 重試路徑未受影響）

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
- **`CLAUDE.md:30` / `AGENTS.md:31`**：`get_market_summary` 被描述為「reads cached market summary from the DB backend」，語意上暗示此工具不打 Finnhub（與相鄰列出的三個 `(hit Finnhub API)` 工具對照更明顯）。但實際上 `generate_market_summary()`（`src/agent/market_summary.py:274-314`）只有在 `backend.get_cached_market_summary(...)` **命中**時才完全略過外部呼叫；一旦當天/該 `days_back`/該新聞集合快取未命中（例如使用者問的 `days_back` 與當日排程用的 `1` 不同，或當天第一次被問到），會呼叫 `fetch_index_quotes()`（`market_summary.py:225-240`），其內部逐一呼叫 `get_quote(symbol)` 對 SPY/QQQ/DIA/IWM 打 Finnhub。既有 QA 報告 `docs/qa-reports/market-summary-cache.md` 第 12 行也明確記載「Cache hit 時避免重複呼叫 fetch_index_quotes()」，反向印證 cache miss 時會呼叫。
  這與本次任務明確要求的「哪些打 Finnhub 的說法要與各 tool 實作相符」直接相關，目前的措辭在 cache miss（相當常見的路徑，例如多日摘要請求）下會誤導讀者。
  建議修法：把該工具的括號說明改成類似「reads news from local DB; on a cache miss also fetches live index quotes via Finnhub (`get_quote`)」，避免暗示它完全不碰 Finnhub。

### 🟢 Suggestions
- `_MAX_TOP_K` 的 clamp（`knowledge_base_tool.py:48`）在 schema 移除 `top_k` 後，目前程式庫中已無任何生產路徑會傳入非預設 `top_k`（已用 `grep -rn "search_knowledge_base\|top_k" src/`確認，僅 `agent.py`/`judge.py`/`knowledge/debug.py` 的同名參數屬於不同函式）；換言之 clamp 邏輯現階段只有測試會觸達。這符合任務明確要求「不得改 clamp 邏輯」的指示，屬合理的防禦性保留（未來若有其他呼叫路徑或工具重新開放 `top_k` 仍可直接受益），不建議在本次處理，僅記錄供之後參考。
- `tests/test_tools.py:545-547` 的 `test_search_knowledge_base_default_top_k_is_three` 斷言 `len(out["results"]) <= 3`，是弱斷言（只驗證上界，無法區分預設值是 3 還是更小的數）。這是既有測試（非本次新增），任務要求只需確認它「真的驗證了不帶 `top_k` 呼叫的行為」——確實是（呼叫時未傳 `top_k`），只是斷言強度偏弱。不在本次改動範圍內，僅供之後強化測試時參考。

### ✅ What looks good
- `knowledge_base_tool.py` 的改動是外科手術式的：只刪除 schema 裡的一個 property，函式簽名、`_DEFAULT_TOP_K`、`_MAX_TOP_K`、`retrieve()` 呼叫、clamp 算式完全未動，符合任務對「哪些不能碰」的明確限制。
- 新增測試 `test_knowledge_base_tool_schema_has_no_top_k` 非空轉：實際檢查 `properties` 字典鍵值與 `required` 陣列內容，若日後有人不小心把 `top_k` 加回 schema 會立即失敗。
- Generator 聲稱「既有測試已涵蓋預設值與 clamp」屬實：`test_search_knowledge_base_default_top_k_is_three`（不帶 `top_k` 呼叫）與 `test_search_knowledge_base_top_k_clamped_to_five`（`top_k=100`）都是直接呼叫 Python 函式而非透過 tool schema，schema 拿掉 `top_k` property 不影響這兩個測試的有效性。
- `ALL_TOOL_SCHEMAS`/`TOOL_FUNCTIONS`（`src/agent/tools/__init__.py`）與 CLAUDE.md/AGENTS.md 新描述逐項核對：五個工具名稱、順序涵蓋（`get_earnings_calendar`、`get_stock_news`、`get_quote`、`get_market_summary`、`search_knowledge_base`）完全吻合；`get_earnings_calendar`/`get_stock_news`/`get_quote` 三者皆用 `finnhub_api_key()` + `client().get(...)` 直接打 Finnhub，已逐一讀原始碼確認（`earnings.py:91`、`news.py:142-148`、`quote.py:25-27`）；`search_knowledge_base` 的「reads local `knowledge/*.md`, no external call」描述完全準確（`agent.knowledge.retrieval` 為 stdlib-only）。
- 用 `git stash` 前後對比自行復核了 sandbox 限制（13 errors）確實是 pre-existing、與本次改動無關，且改動後 `525 passed`（baseline `524 passed`）恰好是 +1（新增測試），無 regression。
- `docs/` 目錄完全未被觸碰，符合「既有 spec/qa-reports/CHANGELOG 是歷史紀錄不得改寫」的要求。
- Generator 誠實回報的殘留（`AGENTS.md` 的 `db/` 段落仍描述純 DynamoDB、未反映 `StorageBackend`/`LocalSQLiteBackend`）判斷合理：這是既有的、不在本次任務範圍內的過時描述（`CLAUDE.md` 的對應段落已在更早的 sprint 更新過），未強行擴大改動範圍是正確的判斷。

## 結論
- 是否可合併：是（🟡 屬於文件措辭精確度問題，不影響功能與測試，建議下次小改順手修正即可，不需要走回 generator 循環）
- 最該先處理的 1–3 件事：
  1. （可選，非阻塞）把 `CLAUDE.md:30` / `AGENTS.md:31` 對 `get_market_summary` 的描述補上「cache miss 時仍會呼叫 Finnhub 取即時指數報價」的但書，避免文件本身重蹈「工具描述與實作不符」的覆轍。
