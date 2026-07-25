# QA Report: knowledge-base

- **Contract**: docs/contracts/knowledge-base.md
- **Date**: 2026-07-25
- **Verdict**: ⚠️ Pass with issues

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| 1. `knowledge/` 目錄 + 3 個種子檔 | ✅ | `indices.md` / `earnings-terms.md` / `analysis-frameworks.md`，共 14 個 `##` section |
| 1. 指數代號對照正確且含「不能直接餵 get_quote」說明 | ✅ | 道瓊/DJIA→DIA、S&P 500/標普→SPY、Nasdaq/那斯達克→QQQ、Russell 2000/羅素→IWM，全部正確；另有一個 section 專講「為什麼不能直接查」 |
| 1. 編寫規則（自成一體/heading 關鍵字/中英同義詞/短而密/無時效性內容） | ✅ | 逐 section 檢閱，無股價/財報數字/年份等時效性內容 |
| 2. 檢索模組僅依賴 stdlib、不 import agent 層 | ✅ | `retrieval.py` 只 `import math/re/collections/dataclasses/functools/pathlib`；`python -c "from agent.tools import TOOL_FUNCTIONS"` + `import agent.agent` 皆正常 |
| 2. tokenizer（lowercase / ASCII word / CJK bigram / len==1 退 unigram / 標點丟棄） | ✅ | 對照 ADR-0008 規則逐條核對程式碼與測試皆符合 |
| 2. section 解析（`##` 切段、前言忽略、`###` 併入、`source` 格式） | ✅ | `source` 格式與 contract 範例完全一致（`indices#道瓊 / DJIA 指數怎麼查價`） |
| 2. BM25（k1=1.5/b=0.75、heading 2x、分數<=0 濾掉、排序） | ✅ | 程式碼常數與公式核對正確；已用多組 query 手動驗證排序 |
| 2. 索引 lazy build + cache、不落地 | ✅ | `@lru_cache(maxsize=1)` on `_get_index`，路徑由 `Path(__file__).resolve().parents[3] / "knowledge"` 推導，可解析到根目錄 |
| 3. `knowledge_base_tool.py`：schema + 函式 | ✅ | 格式與既有工具一致（`input_schema` 而非 `parameters`，與 `market_summary_tool.py`/`quote.py` 相同慣例） |
| 3. 回傳格式 `{"results":[{"source","heading","content"}]}` | ✅ | 手動呼叫驗證 |
| 3. `top_k` 預設 3、clamp [0,5] | ✅ | 手動測試 `top_k=0/-5/1000` 皆正確 clamp（含負數 clamp 到 0，優於「僅上限 clamp」的字面要求） |
| 3. 1500 字元截斷 + 標記 | ✅（程式碼正確，但**無自動測試**） | 手動呼叫 `_truncate("x"*2000)` 驗證輸出長度與截斷標記正確；contract 的 Tests required 列表未強制要求此測試，故不算條目缺陷，列為 🟢 |
| 3. 查無命中回傳 `{"results": []}` | ✅ | |
| 3. schema description 引導模型 | ✅ | 涵蓋術語、代號對照、方法論、來源標註提示 |
| 3. 掛進 `tools/__init__.py`（第六個工具） | ✅ | `ALL_TOOL_SCHEMAS` / `TOOL_FUNCTIONS` 皆含 `search_knowledge_base`，實測 6 個工具 |
| 4. `SYSTEM_INSTRUCTION` surgical append | ✅ | 純 append 在既有字串尾端，未改動既有句子；涵蓋「何時查」「指數別亂猜」「附來源」「查無不編造」四點 |

## Test results
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：251 passed, 12 errors（`tests/test_local_integration.py` 的 `ThreadingHTTPServer` socket bind 被沙盒擋下 `PermissionError`，與本次改動無關的既有限制）
- 非沙盒重跑結果：**263 passed, 0 failed**（與 generator 自報一致）
- 失敗測試：無
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
- **`src/agent/knowledge/retrieval.py`（BM25 對雜訊 bigram 的敏感度）**：generator 自揭「今天美股表現如何」與「今天天氣如何」因 CJK bigram 碰撞（`今天`/`如何`）而誤中，並改寫 `analysis-frameworks.md` 該 section 的 heading 移除「今天」二字讓 contract 指定的測試案例（`retrieve("今天天氣如何") == []`）通過。

  我用 evaluator 自己設計的探測查詢重現了同類問題，證實這不只是單一措辭巧合，而是設計本身在目前語料規模（14 個 section）下的通用弱點：
  ```
  怎麼煮牛肉麵 -> ['indices#Russell 2000...怎麼查價', 'indices#Nasdaq...怎麼查價', 'indices#道瓊 / DJIA...怎麼查價']
  蘋果派怎麼做 -> 同上三筆
  今天股市休市嗎 -> ['analysis-frameworks#怎麼看大盤整體市況 market overview']
  ```
  原因：語料極小時，罕見詞（如「怎麼」若只出現在少數 heading）在 BM25 idf 公式 `log((N-df+0.5)/(df+0.5)+1)` 下反而拿到**偏高**的 idf（df 越小 idf 越大），而 heading 又依編寫規則刻意寫「使用者會問的關鍵字」（怎麼查、是什麼、如何…）並加權 2 倍——這組合讓常見疑問句式的功能詞在小語料下被當成「稀有且重要」的詞，只要 query 與某個 heading 共享 1-2 個這類 bigram，分數就會 > 0 而被判定為命中，即便語意完全無關。

  這是 ADR-0008 已知且接受的 trade-off 的一個尚未明確寫出的具體表現形式（ADR 提到「bigram 對單字詞精度較低」「無語意泛化」，但未特別點出「heading 常見疑問句式 bigram 造成無關查詢誤中」這個情境）。**contract 字面要求（`retrieve("今天天氣如何") == []`）已滿足，且該行為選擇是 ADR 明確定案、非本次可逆的實作缺陷**，因此不判 Critical；但目前的「修法」只讓這一個具體例句通過，未觸及通用問題本身，建議至少在 ADR 或程式碼註解中明確記錄此已知限制（目前 ADR 的 Consequences 段落可以再補一句），並在後續 sprint 視實際使用情況考慮：拉高分數門檻（例如相對 top score 或最小 overlap token 數）、或替 heading 中的高頻疑問句式（怎麼/如何/是什麼/今天等）建立極小 stopword 名單。
  影響範圍：由於 `search_knowledge_base` 是否被呼叫仍由 LLM 依 prompt 判斷（只在財報術語/指數代號/方法論情境觸發），非所有對話都會走到這個工具，實務衝擊有限，但一旦誤觸發，工具會回傳看似有信心（含來源標註）但語意無關的結果，可能誤導 LLM 引用錯誤來源作答。

### 🟢 Suggestions
- **`src/agent/tools/knowledge_base_tool.py:14-15`**：`_MAX_CONTENT_CHARS`/截斷邏輯（`_truncate`）沒有對應的單元測試（`tests/test_tools.py`、`tests/test_knowledge_retrieval.py` 皆未覆蓋）。contract 的 `Tests required` 清單未強制列出，故不影響驗收結論，但既然這是明確的 deliverable 行為（1500 字元安全上限），建議補一個直接呼叫 `_truncate` 或用長字串 monkeypatch section content 的測試，避免未來改動時無回歸保護。
- **`src/agent/tools/knowledge_base_tool.py:35-38`**：`top_k` 的 `input_schema` 未像 `market_summary_tool.py` 的 `days_back` 一樣宣告 `"minimum"/"maximum"`。目前是靠函式內 `max(0, min(top_k, 5))` clamp，行為正確，但 schema 加上 min/max 能讓模型在選參數時就有提示，屬於錦上添花，不影響行為正確性。
- **排序品質（非 contract 要求，僅供參考）**：我另外測試 `"amc 是什麼意思"`（非 contract 指定的 `"amc 是什麼"`）時，正確 section（`bmo amc dmh 財報公布時段代碼是什麼`）落在 top-3 的第 3 名而非 top-1（因為 `"營收 beat/miss...是什麼意思"` section 與 query 共享 4 個 bigram `是什/什麼/麼意/意思`，蓋過只共享 `amc` 一個 word token 的正確 section）。用 contract 指定的原始 query `"amc 是什麼"` 測試則 top-1 正確命中，不影響驗收。

### ✅ What looks good
- tokenizer / section 解析 / BM25 三層邏輯乾淨對應 ADR-0008 規格，程式碼量精簡（`retrieval.py` 175 行含 docstring），無多餘抽象。
- 種子知識庫內容密度高、無時效性內容、中英同義詞落實（如「道瓊 Dow Jones DJIA」「guidance 財測/展望」），指數↔ETF 對照全部正確。
- `knowledge_base_tool.py` 與既有 `market_summary_tool.py` 風格一致（`input_schema` 命名、docstring 說明是否會循環引入），可讀性好。
- `tools/__init__.py`、`agent.py` 的改動都是 surgical append，未動到既有 5 個工具與既有句子語意。
- 測試設計扎實：`test_knowledge_retrieval.py` 20 案例涵蓋 tokenizer 全部規則、section 解析全部邊界（前言忽略/`###`併入/無 `##` 回空）、BM25 命中與無命中、`top_k` clamp；`test_tools.py` 新增的 8 案例額外驗證了 `_run_tool` dict passthrough 與循環引入防護，是本次最容易出錯但驗證最到位的部分。
- clamp 行為比字面要求更穩健：contract 只要求「上限被 clamp」，實作連負數/0 也一併妥善處理為空結果，無 IndexError 或例外。

## 結論
- 是否可合併：**是**（Pass with issues；僅 🟡 1 條 + 🟢 3 條，無 🔴，無 contract 條目 ❌）
- 最該先處理的 1–3 件事：
  1. 在 ADR-0008 的 Consequences 補一句，明確記錄「小語料 + heading 常見疑問句式 bigram 可能讓無關查詢誤中」這個已驗證的具體限制，避免未來被誤認為未知 bug。
  2. （可選，非阻塞）補 `_truncate` 的單元測試。
  3. （可選，非阻塞）後續若語料擴大，重新評估 BM25 分數門檻或 heading 權重是否仍合適。
