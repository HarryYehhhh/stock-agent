# Spec: Wiki/BM25 Playbook Grounding（pre-injection 工具路由指引）

## Goal
把工具路由指引從 `SYSTEM_INSTRUCTION` 拆成可檢索的 playbook wiki，在進 tool loop 前由程式碼用 user message 檢索並注入 context，讓正確的工具選擇不再依賴模型自覺。

## Background
Production 事件：使用者問「本週有什麼公司發布財報」，模型卻附上歷史對話裡的 AAPL 分析師評級（答非所問 + 歷史污染）。已修 IAM bug（PR #30）、加 prompt 防污染指示（PR #31）、清污染歷史。本 sprint 是根本性改善：不再把一大段工具選擇策略塞進 system prompt 讓模型自己判斷，而是把它拆成分類 playbook，開場依當前問題檢索命中的那一類注入，縮小模型判斷面、也讓指引成為可測的單一真相來源。

相關既有資產（沿用、不重寫）：
- `src/agent/knowledge/retrieval.py`：stdlib-only BM25 + CJK bigram 檢索（ADR-0008）。
- `knowledge/*.md`：術語 / 指數對照 / 分析框架知識庫，由 `search_knowledge_base` 工具查詢。
- `src/agent/prompts.py` 的 `SYSTEM_INSTRUCTION`：目前工具選擇策略與通用行為混在一起。

## User stories
- As a 使用者, I want 問「本週有什麼公司發布財報」時系統只走財報行事曆邏輯, so that 不會夾帶我沒問的個股或歷史數據。
- As a 維護者, I want 工具路由規則集中在人類可讀、可檢索、可測的 playbook 檔, so that 改規則只改一處、且有測試鎖住。
- As a 維護者, I want 每輪注入與每次工具呼叫都有 structured log, so that 線上能診斷「這題有沒有注入、注入了哪一類、命中分數多少」。

## Design（已定案，本 spec 補齊細節）

### 1. Playbook 檔案
三份 markdown 放 `knowledge/playbooks/`（`knowledge/` 根目錄下的**子目錄**，天然不被既有 `knowledge/*.md` glob 掃到）：

- `market-trends.md`：大盤 / 市場摘要類問題 → `get_market_summary`、`days_back` 對應規則。
- `earnings.md`：財報行事曆 / 個股財報預期 → `get_earnings_calendar` / `get_analyst_estimates`（含「本週」等時間詞的未來視角日期區間規則）。
- `stock-news.md`：個股新聞 → `get_stock_news`、歸納整合規則。

每個 `## ` heading = 一類問題，section 內含四個固定小節（用途明確、供 BM25 命中與模型遵循）：
- **觸發語**：給 BM25 的關鍵詞句（使用者會怎麼問這一類）。
- **工具指引**：呼叫哪個 / 哪些工具、參數怎麼傳。
- **回覆要求**：這一類該怎麼組織回覆、要標註什麼。
- **禁止**：明確不得做的事（如不得附上使用者未提及的個股、不得引用歷史對話數據）。

內容來源 = 把現行 `SYSTEM_INSTRUCTION` 的「工具選擇策略」段落拆遷進 playbook，成為單一真相來源；`SYSTEM_INSTRUCTION` 只留通用行為規範。

### 2. SYSTEM_INSTRUCTION 逐句拆遷分類（單一真相來源，不得語意重複兩地）
下表逐句對 `src/agent/prompts.py` 現行 `SYSTEM_INSTRUCTION`（依現況行序）分類。`MOVE` = 從 SYSTEM_INSTRUCTION 刪除、內容改寫進對應 playbook；`STAY` = 留在 SYSTEM_INSTRUCTION。

| # | 現行內容（摘要） | 去向 | 備註 |
|---|---|---|---|
| S1 | 「你是專業美股財報分析助理」 | STAY | 角色 |
| S2 | 「一律使用繁體中文回應」 | STAY | 語言（通用） |
| S3 | 「新聞英文標題與摘要主動翻譯成繁體中文」 | MOVE → stock-news | 回覆要求（新聞專屬） |
| S4 | 新聞不逐條翻譯、依主題歸納整合、忽略行銷導流條目 | MOVE → stock-news | 回覆要求 + 禁止 |
| S5 | Telegram Markdown 格式（`*text*`、不要 `**`/`__`） | STAY | 格式（通用） |
| S6 | EPS 用 `$0.89`、營收用 B/M 縮寫 | STAY | 格式（通用；設計明列保留） |
| S7 | 分析師評級顯示 買入/持有/賣出 數量分佈 | MOVE → earnings | 回覆要求，緊耦合 `get_analyst_estimates` 輸出 |
| S8 | Finnhub earnings `hour` 代碼 bmo=盤前/amc=盤後/dmh=盤中 | STAY | 術語（設計明列「術語」保留）；earnings 回覆要求會再引用此對照 |
| S9 | 需要即時市場資料主動呼叫工具、不憑空猜數字 | STAY | 通用行為 |
| S10 | 明確股票代碼問財報/分析師預期/EPS → 同時呼叫 `get_analyst_estimates` + `get_earnings_calendar`，查特定 ticker 務必傳 `symbol` | MOVE → earnings | 工具指引 |
| S11 | 「本週/近期/接下來/最近」問財報行事曆 → 未來視角 `date_from`=今天、`date_to`=今天+7；明確查過去才用過去區間 | MOVE → earnings | 觸發語 + 工具指引 |
| S12 | 查即時股價 / 當日漲跌 → `get_quote` | STAY | 個股即時報價路由；無專屬 playbook，屬通用工具路由（見 Open questions 決策 D1） |
| S13 | 問市場摘要 / 大盤整體 → `get_market_summary`，不要用 `get_quote` 猜指數代號 | MOVE → market-trends | 工具指引 + 禁止 |
| S14 | 依時間詞傳 `days_back`（今天/本日=1；本週/近一週/過去七天=7；無時間詞預設 1）；上限 7、無超過 7 天資料 | MOVE → market-trends | 工具指引 |
| S15 | 問「本月」或更長期間 → 一樣 `days_back=7`，回覆說明僅涵蓋最近 7 天 | MOVE → market-trends | 工具指引 + 回覆要求 |
| S16 | 回覆明說摘要實際涵蓋的日期區間 | MOVE → market-trends | 回覆要求 |
| S17 | 轉述大盤指數漲跌數字須保留「當日漲跌」標註 | MOVE → market-trends | 回覆要求 |
| S18 | 財報尚未公布（晚於今天）→ 回覆提醒預計發布日期與時段 | MOVE → earnings | 回覆要求 |
| S19 | 不要僅因 earnings calendar 查無就回「找不到資料」（分析師預期常早於財報日） | MOVE → earnings | 工具指引 + 回覆要求 |
| S20 | 財報行事曆每筆標日期 + 盤前/盤後/盤中（bmo/amc/dmh）；`hour` 空/缺標「時段未定」不省略 | MOVE → earnings | 回覆要求（S8 提供術語對照，此處為呈現規則） |
| S21 | 只答當前問題、依本次工具結果、不引用/模仿歷史舊回答與數據、未提及個股不主動附上 | STAY | 防歷史污染（PR #31，通用） |
| S22 | 遇術語/指數對照/方法論 → 先呼叫 `search_knowledge_base` | STAY | 知識庫引用規範（設計明列保留） |
| S23 | 指數代號不要亂猜直接 `get_quote`，先查知識庫取 ETF 代號再查價 | STAY | 知識庫引用規範（與 S13 相關但屬 `search_knowledge_base` 路由，留在 system） |
| S24 | 引用知識庫內容附來源標註（`檔名#標題`） | STAY | 知識庫引用規範 |
| S25 | 知識庫查無相關內容明說不確定/沒有、不編造 | STAY | 知識庫引用規範 |

**拆遷原則**：MOVE 的內容從 `SYSTEM_INSTRUCTION` 移除後，不得在 system 與 playbook 兩地語意重複；playbook 是唯一真相來源。S8（術語代碼對照）保留在 system，earnings playbook 的 S20 回覆要求可直接寫「盤前/盤後/盤中」而不需重述代碼定義。

### 3. Pre-injection 檢索流程
在 `run_agent()` 進 tool loop 之前：
1. 以**當前 user message**（不含 history，避免再度引入污染）對 playbook 索引跑 BM25。
2. `top_k = 2`；套用新增的 `MIN_SCORE` 門檻：分數 `>= MIN_SCORE` 的 section 才注入。
3. 若有命中 section：把它們組成一段 **system context** 注入（附來源標註 `檔名#標題`），置於基礎 system message 之後、history 之前。
4. 若全數低於 `MIN_SCORE`（或無命中）：不注入，fallback 到目前（無 playbook）的行為，run_agent 照常執行。

`MIN_SCORE` 為經驗常數（BM25 分數未正規化），由 generator 依實測選定並以註解說明；測試鎖「行為」（正例注入、負例不注入）而非鎖具體數字。

### 4. 兩套索引隔離
- playbook 索引來源 = `knowledge/playbooks/`；`search_knowledge_base` 的術語索引來源 = `knowledge/*.md`（不遞迴，不含子目錄）。
- 既有 `retrieval.py` 的 `_load_sections` / `_build_index` / `_get_index` 需**參數化索引來源目錄**，兩個索引各自 `lru_cache`、互不污染。
- `search_knowledge_base` 現行呼叫 `retrieve(query, top_k)` 的行為與結果不得改變（預設索引仍為 `knowledge/*.md`）。

### 5. 觀測性
- **`playbook_injection` structured log**：每次 `run_agent()` 呼叫記一筆——`query`、命中的 section id 清單、各自分數、`injected`（bool）。
- **成功 tool 呼叫 log**：`agent.py` 的 `_run_tool` 目前只 log 失敗（`log.warning`）；補成功時 `log.info`——工具 `name`、`args`（沿用 `_sanitize_error` 遮蔽 token）、回傳筆數 / 型別。
- **debug CLI**：`python -m agent.knowledge.debug "本週財報"` 印出 top-k section 的來源、分數；可指定查 playbook 索引或術語 KB 索引。

### 6. 斷詞策略
維持既有 CJK bigram（ADR-0008，stdlib-only）。CKIP / jieba 已與使用者討論，結論不採用（新增第三方依賴、與離線可測目標衝突；小語料 bigram 已足夠）。此決策連同 pre-injection 架構記於 ADR-0009。

## Acceptance criteria
- [ ] `knowledge/playbooks/` 有三份檔（market-trends / earnings / stock-news），每個 `## ` section 含 觸發語 / 工具指引 / 回覆要求 / 禁止 四小節。
- [ ] `SYSTEM_INSTRUCTION` 依上表拆遷：MOVE 項已移除，STAY 項保留；system 與 playbook 無語意重複。
- [ ] `retrieval.py` 索引來源可參數化；playbook 與術語 KB 為兩套獨立 `lru_cache` 索引，互不污染。
- [ ] `run_agent()` 開場用 user message 檢索 playbook，`top_k=2` + `MIN_SCORE` 門檻；命中則注入含 `檔名#標題` 來源標註的 system context，未達門檻 fallback 到無 playbook 行為。
- [ ] 「本週有什麼公司發布財報」→ 命中 earnings 行事曆 section，**不**命中 stock-news section。
- [ ] `playbook_injection` log 每次 run 記一筆（query / 命中 id / 分數 / injected）；`_run_tool` 成功時 log name / args / 回傳筆數或型別。
- [ ] `python -m agent.knowledge.debug "<query>"` 印 top-k + 分數，可切換 playbook / 術語 KB。
- [ ] `test_prompts.py` 中針對已 MOVE 條目的 substring 斷言，改為斷言在對應 playbook 檔案；既有測試不回歸。
- [ ] `PYTHONPATH=src uv run pytest tests/ -v` 全綠。
- [ ] chat_cli / local_server 實測可見注入 log。

## Non-goals / Out of scope
- DynamoDB 資料快照 dump 成帶日期 md 的稽核機制、離線 LLM-judge 驗證管線 → **phase 2**。
- scheduler 寫入端新聞預篩選 → **另案**。
- embeddings / 向量檢索 / 任何新第三方套件（維持 ADR-0008）。
- 改用 jieba / CKIP 斷詞。
- 把 playbook 做成第 7 個工具讓模型自己呼叫（正是「依賴模型自覺」的失敗模式，本 sprint 反其道而行）。
- Playbook 內容線上熱更新 / reload（process 內快取，重啟才重讀）。
- `get_quote` 單股路由與 `search_knowledge_base` 路由拆成 playbook（維持留在 SYSTEM_INSTRUCTION，見 D1）。

## Open questions / 決策
- **D1（已決）**：`get_quote`（個股即時股價，S12）與 `search_knowledge_base` 路由（S22–S25）無專屬 playbook。依設計「保留清單」明列「知識庫引用規範」保留，且此兩類非事件高風險類別、屬低風險通用工具路由——**留在 `SYSTEM_INSTRUCTION`**，不新增 playbook。若日後這兩類也出現路由誤判，再另立 playbook。
- **D2（交由 generator）**：`MIN_SCORE` 具體數值由 generator 依實測選定（正例「本週財報」注入、負例如「今天天氣如何」不注入），以程式碼註解記錄取值理由；測試鎖行為不鎖數字。
- **D3（交由 generator）**：注入 system context 的確切措辭（前綴句、section 之間分隔）由 generator 決定，但必須含每筆來源 `檔名#標題` 標註，且明確引導模型「依此 playbook 選工具與組織回覆」。
