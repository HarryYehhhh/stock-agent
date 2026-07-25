# Spec: Markdown 知識庫 + search_knowledge_base 工具

## Goal
給 agent 一個本地 Markdown 知識庫與第六個工具 `search_knowledge_base`，讓 LLM 回答領域問題（指數代號對照、財報術語、分析框架）時能檢索靜態知識並在回覆中附來源，提升正確性且可驗證。

## Background / 問題
- 實際發生過的 bug：使用者問「道瓊怎麼查」，agent 直接用 `get_quote` 猜 `DJIA` 之類的代號，拿到錯誤或空報價。正確做法是查 ETF 替代代號（道瓊→`DIA`、S&P 500→`SPY`、那斯達克→`QQQ`）。這類「代號對照」「術語解讀」「方法論」屬於穩定的領域知識，適合放靜態知識庫，而非每次靠 LLM 記憶或即時 API。
- 目前 agent 有 5 個工具（earnings/estimates/news/quote/market_summary），全部打即時 API，沒有靜態知識來源。

## User stories
- As a 使用者, I want 用中文問「道瓊怎麼查」, so that agent 能檢索到指數代號對照，告訴我用 `DIA` ETF 查價並附來源，而不是用 `get_quote` 猜 `DJIA` 拿到錯誤報價。
- As a 使用者, I want 問「EPS beat 是什麼」「amc 是什麼意思」, so that agent 能從知識庫取得術語解讀並附來源。
- As a 使用者, I want 問「怎麼判斷一檔股票財報好不好」這類方法論問題, so that agent 能引用知識庫的分析框架，說明該組合哪些現有工具。
- As a 維護者, I want 用純 Markdown 檔維護領域知識、一個 `##` section 就是一個檢索單位, so that 我不必寫 code 就能新增/修正知識。

## Acceptance criteria
- [ ] 專案內有 `knowledge/` 目錄，含 2-3 個種子 `.md` 檔（指數代號對照、財報術語、分析框架）。
- [ ] 種子檔遵守編寫規則：一個 `##` section = 一個自成一體的檢索單位；heading 放使用者會問的關鍵字；中英同義詞寫進內文；section 短而密；不放時效性內容。
- [ ] 指數代號對照 section 明確涵蓋 DJIA/道瓊 → `DIA`、S&P 500 → `SPY`、Nasdaq/那斯達克 → `QQQ`，並說明「指數不能直接餵 get_quote，要用 ETF 替代查價」。
- [ ] 新增第六個工具 `search_knowledge_base`，掛進 `tools/__init__.py` 的 `ALL_TOOL_SCHEMAS` / `TOOL_FUNCTIONS`。
- [ ] 用中文查詢「道瓊怎麼查」呼叫 `search_knowledge_base`，top-1 命中指數代號對照 section，回傳內容含 `DIA`，且帶來源標註 `檔名#標題`。
- [ ] 查詢與知識庫無關的內容（例如「今天天氣如何」）回傳空結果（不硬塞不相關 section）。
- [ ] 索引在 process 內 lazy build 並 cache，不落地任何索引檔、不新增第三方套件、不用 embeddings。
- [ ] `SYSTEM_INSTRUCTION` 補上：何時該查知識庫、引用時附來源、查無就明說不編造、指數代號不要用 get_quote 亂猜。
- [ ] `make test` 全綠；新增測試覆蓋斷詞、section 解析、BM25 檢索命中/無命中、來源標註、工具註冊。

## Non-goals
- 不處理部署：md 檔進 Lambda image、雲端路徑解析都留給未來。
- 不用 embeddings / 向量檢索 / 任何額外第三方套件（BM25 自含實作）。
- 索引不持久化（不寫索引檔到磁碟）。
- 不放時效性/會過期的內容（股價、當期財報數字、當年 guidance 具體值等）。
- 不改動既有 5 個工具的行為。
- 知識庫內容不做線上自動更新機制。

## Open questions
- （無阻擋性問題；斷詞與檢索策略已於 contract / ADR-0008 定案。）
