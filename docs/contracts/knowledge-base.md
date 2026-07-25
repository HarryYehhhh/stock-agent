# Contract: knowledge-base

對應 spec: docs/specs/knowledge-base.md
相關決策: docs/decisions/0008-knowledge-base-retrieval-strategy.md

> 路徑用「應用程式碼路徑」等中性描述；generator 對照 CLAUDE.md 解讀為具體值。
> 本專案：應用程式碼在 `src/agent/`、工具在 `src/agent/tools/`、測試在 `tests/`、測試指令 `PYTHONPATH=src uv run pytest tests/ -v`（或 `make test`）。

## Deliverables

### 1. 知識庫目錄與種子檔
- [ ] 在專案根目錄建立 `knowledge/` 目錄，放靜態領域知識 `.md` 檔。
- [ ] 種子檔 2-3 個，內容方向：
  - **指數代號對照**（例：`knowledge/indices.md`）：至少一個 section 說明「指數（DJIA/道瓊、S&P 500、Nasdaq/那斯達克等）不能直接餵 `get_quote`，要用 ETF 替代查價」，並列出對照：道瓊/DJIA → `DIA`、S&P 500/標普 → `SPY`、Nasdaq 100/那斯達克 → `QQQ`（可再補 Russell 2000 → `IWM` 等）。heading 要放使用者會問的字（例如「道瓊 / DJIA 指數怎麼查價」）。
  - **財報術語解讀**（例：`knowledge/earnings-terms.md`）：EPS beat/miss、guidance（財測/展望）、營收 beat/miss、bmo（盤前）/amc（盤後）/dmh（盤中）等，每個術語自成 section，中英同義詞寫進內文。
  - **分析框架**（例：`knowledge/analysis-frameworks.md`）：常見問題怎麼組合現有工具回答（例如「怎麼判斷財報好不好」→ 用 `get_analyst_estimates` 取共識 + `get_earnings_calendar` 查發布日 + `get_stock_news` 看市場反應）。
- [ ] 種子內容遵守編寫規則（已與使用者確認）：
  - 一個 `##`（level-2）section = 一個檢索單位，內容自成一體。
  - heading 放使用者會問的關鍵字。
  - 中英同義詞寫進內文（例如「道瓊 Dow Jones DJIA」）。
  - section 短而密。
  - 不放時效性內容（不寫具體股價、當期財報數字、當年 guidance 數值）。

### 2. 知識庫索引 / 檢索模組
- [ ] 在應用程式碼下新增知識庫模組（建議 `src/agent/knowledge/`，內含斷詞、section 解析、BM25 索引、檢索函式；也可單檔，由 generator 判斷）。**此模組只依賴標準函式庫**（`re`、`pathlib`、`math`、`functools` 等），不得 import `agent.agent` / `agent.market_summary` / `agent.tools`，以免循環引入。
- [ ] **斷詞（tokenizer）** 依 ADR-0008：全程 lowercase；ASCII 英數 run → word token；CJK run → 相鄰字元 bigram（run 長度 1 時輸出 unigram）；標點/空白為分隔符丟棄。建索引與查詢共用同一 tokenizer。
- [ ] **section 解析**：讀 `knowledge/*.md`，以 `##`（level-2 heading）切 section。每個 section 一筆記錄，至少含：來源檔、heading 文字、section 內文（heading + 其下到下一個 `##` 或 EOF 的內容）、來源標註 `source`（格式 `<檔名>#<標題>`，檔名用不含副檔名的 stem，例如 `indices#道瓊 / DJIA 指數怎麼查價`）。
  - 第一個 `##` 之前的內容（`#` 文件標題、前言）不作為檢索單位，忽略。
  - `###` 及更深的 heading 不另外切 section，併入所屬 `##` section。
- [ ] **檢索（retrieve）**：自含 BM25（`k1=1.5`、`b=0.75`），heading tokens 加權（計入 2 次，見 ADR-0008）。回傳依分數排序的 top-k section，**濾掉分數 <= 0**。
- [ ] **索引 lazy build + cache**：首次檢索時讀檔建索引，用 `@lru_cache`（或等效 module-level 快取）於 process 內快取，不落地索引檔。`knowledge/` 目錄路徑由模組位置推導（deployment/Lambda 路徑解析為 out of scope，本地能解析到根目錄 `knowledge/` 即可）。

### 3. 第六個工具 search_knowledge_base
- [ ] 在 `src/agent/tools/` 新增 `knowledge_base_tool.py`（模式參考既有工具與 `market_summary_tool.py`），提供：
  - schema 常數 `KNOWLEDGE_BASE_TOOL`（`name` = `search_knowledge_base`）。
  - 函式 `search_knowledge_base(query: str, top_k: int = 3) -> dict`。
- [ ] 行為：呼叫檢索模組取 top-k，回傳
  ```
  {"results": [
      {"source": "<檔名>#<標題>", "heading": "<標題>", "content": "<section 內文>"},
      ...
  ]}
  ```
- [ ] `top_k` 預設 3、上限夾在 5（越界 clamp，不報錯）。
- [ ] token 量控制：靠「section 短而密」的編寫規則為主；另加安全上限——單一 section content 超過約 1500 字元時截斷（附截斷標記）。
- [ ] 查無命中（所有 section 分數 <= 0）→ 回傳 `{"results": []}`（讓 LLM 知道知識庫沒有相關內容）。
- [ ] schema `description` 要引導模型：用於術語解讀、指數/代號對照、方法論問題；並說明會回傳來源標註。
- [ ] 掛進 `tools/__init__.py`：加入 `ALL_TOOL_SCHEMAS` 與 `TOOL_FUNCTIONS`（top-level import `knowledge_base_tool`；因該模組只依賴 stdlib，不會觸發既有 `market_summary_tool` 那類循環引入，但仍**不要**在知識庫模組反向 import agent 層）。

### 4. Prompt 調整
- [ ] `agent.py` 的 `SYSTEM_INSTRUCTION` 增補（不改動既有句子語意，surgical append）：
  - 何時查知識庫：遇到財報術語（EPS beat/miss、guidance、bmo/amc/dmh）、指數/代號對照（道瓊、S&P 500、那斯達克怎麼查價）、分析方法論問題時，先呼叫 `search_knowledge_base`。
  - 指數代號不要用 `get_quote` 亂猜（例如 DJIA），先查知識庫取得對應 ETF 代號再查價。
  - 引用知識庫內容時，在回覆附上來源標註（`檔名#標題`）。
  - 知識庫查無相關內容時，明說不確定 / 知識庫沒有，不要編造。

## Out of scope
- 部署：md 檔進 Lambda container image、雲端工作目錄下的 `knowledge/` 路徑解析。
- embeddings / 向量檢索 / 任何新第三方套件。
- 索引持久化（寫索引檔到磁碟或 DB）。
- 改動既有 5 個工具、既有 SYSTEM_INSTRUCTION 既有句子的語意。
- 知識庫內容線上自動更新、reload 機制（process 內快取，重啟才重讀即可）。

## Tests required
- [ ] **tokenizer**：英文 word 抽取、CJK bigram、中英混合、lowercase、len==1 CJK 退化為 unigram、標點被丟棄。
- [ ] **section 解析**：以 `##` 正確切段；`source` 標註格式 `<stem>#<heading>`；第一個 `##` 前的前言被忽略；`###` 併入父 section。
- [ ] **BM25 檢索**：
  - 中文查詢「道瓊怎麼查」→ top-1 命中指數代號對照 section，內文含 `DIA`，`source` 指向該 section。
  - 至少一個財報術語查詢（例如「amc 是什麼」或「EPS beat」）命中對應 section。
  - 無關查詢（例如「今天天氣如何」）→ 回傳空結果。
  - top_k 生效且上限被 clamp。
- [ ] **工具層**：`search_knowledge_base` 回傳 dict（結構含 `results` 與每筆 `source`）；透過 `_run_tool` / `TOOL_FUNCTIONS` 正確串接（dict 直接通過不被 `{"result": ...}` 包裹）；`search_knowledge_base` 出現在 `ALL_TOOL_SCHEMAS` 名稱集合與 `TOOL_FUNCTIONS`。
- [ ] **無循環引入**：`from agent.tools import TOOL_FUNCTIONS`（或 import agent.agent）可正常載入。
- [ ] 種子檔健全性（輕量）：每個種子檔至少解析出 1 個 section、每個 section 有非空 heading 與非空內文。
- [ ] 測試離線、決定性、不打網路、不碰真實 AWS（沿用既有測試慣例）。

## Definition of done
- 全部 deliverable 打勾。
- `make test`（`PYTHONPATH=src uv run pytest tests/ -v`）全綠。
- evaluator 的 QA report 無 P0/P1。
- 手動驗收情境成立：用中文問「道瓊怎麼查」，檢索能命中指數代號對照 section、回傳含 `DIA` 且附來源 `檔名#標題`。
