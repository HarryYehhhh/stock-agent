# Contract: playbook-grounding

對應 spec: docs/specs/playbook-grounding.md
相關決策: docs/decisions/0009-playbook-grounding-pre-injection.md（沿用 0008 檢索核心）

> 路徑用中性描述，generator 對照 CLAUDE.md 解讀為具體值。
> 本專案：應用程式碼 `src/agent/`、工具 `src/agent/tools/`、檢索模組 `src/agent/knowledge/`、知識庫資料在專案根 `knowledge/`、測試 `tests/`、測試指令 `PYTHONPATH=src uv run pytest tests/ -v`（或 `make test`）。

## Deliverables

### 1. Playbook 檔案（`knowledge/playbooks/`）
- [ ] 建 `knowledge/playbooks/` 子目錄，放三份 md：`market-trends.md`、`earnings.md`、`stock-news.md`。
- [ ] 每份至少 1 個 `## `（level-2）section；每個 section 含四個小節：**觸發語**、**工具指引**、**回覆要求**、**禁止**。
- [ ] 內容來源 = 依 spec「SYSTEM_INSTRUCTION 逐句拆遷分類」表把 MOVE 條目改寫進對應 playbook：
  - `market-trends.md`：S13（`get_market_summary`、不用 `get_quote` 猜指數）、S14（`days_back` 時間詞對應與上限 7）、S15（本月/更長期間傳 7 並說明僅涵蓋 7 天）、S16（明說涵蓋日期區間）、S17（指數漲跌保留「當日漲跌」標註）。
  - `earnings.md`：S7（分析師評級 買入/持有/賣出 分佈）、S10（明確代碼 → 同時 `get_analyst_estimates`+`get_earnings_calendar`、傳 `symbol`）、S11（本週/近期/接下來/最近 → 未來視角 `date_from`=今天、`date_to`=今天+7；明確查過去才用過去區間）、S18（未公布提醒預計日期時段）、S19（勿僅因行事曆查無就回「找不到資料」）、S20（每筆標日期+盤前/盤後/盤中，缺值標「時段未定」）。
  - `stock-news.md`：S3（英文標題摘要主動翻譯）、S4（不逐條翻譯、依主題歸納整合、忽略行銷導流條目）。
- [ ] 觸發語需涵蓋各類的真實問法關鍵詞句，確保 BM25 命中（例如 earnings 需含「本週」「財報」「發布」等；market-trends 需含「大盤」「市場摘要」等；stock-news 需含「新聞」「消息」等）。
- [ ] 遵守既有 knowledge 編寫規則：section 短而密、中英同義詞寫進內文、不放時效性數字。

### 2. SYSTEM_INSTRUCTION 拆遷（`src/agent/prompts.py`）
- [ ] 依 spec 分類表：移除所有 MOVE 條目（S3、S4、S7、S10、S11、S13–S20），保留 STAY 條目（S1、S2、S5、S6、S8、S9、S12、S21、S22–S25）。
- [ ] 拆遷後 system 與 playbook **無語意重複**；`SYSTEM_INSTRUCTION` 仍為非空字串、語句通順。
- [ ] 不改動 STAY 條目的語意（surgical）；`SUMMARY_SYSTEM_PROMPT_TEMPLATE` / `summary_system_prompt` / `COMPANY_PROMPT_TEMPLATE` 完全不動。

### 3. 檢索模組參數化 + 隔離（`src/agent/knowledge/retrieval.py`）
- [ ] `_load_sections` / `_build_index` / `_get_index` 參數化索引來源目錄；playbook 索引來源 = `knowledge/playbooks/`，術語 KB 索引來源 = `knowledge/*.md`（不遞迴）。兩索引各自 `lru_cache`、互不污染。
- [ ] 維持 stdlib-only（ADR-0008）：不得 import `agent.agent` / `agent.market_summary` / `agent.tools`。
- [ ] 對外暴露「帶分數」的檢索（例如 `retrieve_scored(query, top_k, source_dir=...) -> list[(Section, float)]`），供注入層做 `MIN_SCORE` 判斷、logging 與 debug CLI 共用。
- [ ] `search_knowledge_base` 現行呼叫（`retrieve(query, top_k)`）之行為、預設索引（`knowledge/*.md`）、回傳結構**完全不變**。

### 4. Pre-injection（`src/agent/agent.py` `run_agent()`）
- [ ] 進 tool loop 前，用**當前 user message**（不含 history）對 playbook 索引檢索，`top_k=2`。
- [ ] 新增 `MIN_SCORE` 常數：僅注入分數 `>= MIN_SCORE` 的 section；值由實測選定並加註解說明。
- [ ] 有命中 → 組 system context 注入，置於基礎 system message 之後、history 之前；內容含每筆來源標註 `檔名#標題`，並引導模型依 playbook 選工具與組織回覆。
- [ ] 無命中 / 全低於門檻 → 不注入，run_agent 照現行流程執行（fallback）。

### 5. 觀測性
- [ ] `run_agent()` 每次呼叫記一筆 `playbook_injection` structured log：`query`、命中 section id 清單、各分數、`injected`（bool）。
- [ ] `_run_tool` 成功時 `log.info`：工具 `name`、`args`（經 `_sanitize_error` 遮蔽 token）、回傳筆數 / 型別；失敗時既有 `log.warning` 行為不變。
- [ ] 新增 debug CLI `src/agent/knowledge/debug.py`，支援 `python -m agent.knowledge.debug "<query>"`：印 top-k section 的 `source`、分數；可切換查 playbook 索引或術語 KB 索引（旗標形式由 generator 定）。

## Out of scope
- phase 2：DynamoDB 快照 dump 帶日期 md 稽核、離線 LLM-judge 驗證管線。
- 另案：scheduler 寫入端新聞預篩選。
- embeddings / 向量檢索 / 新第三方套件；jieba / CKIP 斷詞。
- 把 playbook 做成第 7 個工具、或為 `get_quote` / `search_knowledge_base` 新增 playbook。
- playbook 熱更新 / reload；部署路徑（Lambda container `knowledge/playbooks/` 路徑解析）比照既有 `knowledge/` 處理，不在本 sprint 特別處理。

## Tests required
- [ ] **playbook 檢索命中**：「本週有什麼公司發布財報」→ top-1 為 `earnings.md` 的行事曆 section；結果**不含** `stock-news.md` 任何 section。
- [ ] 至少一個 market-trends 正例（例如「今天大盤怎樣」）→ 命中 `market-trends.md`；一個 stock-news 正例（例如「AAPL 有什麼新聞」）→ 命中 `stock-news.md`。
- [ ] **MIN_SCORE fallback**：不相關 query（例如「今天天氣如何」）檢索分數低於 `MIN_SCORE` → 不注入；`run_agent`（mock Groq）仍正常執行、system messages 不含 playbook context。
- [ ] **注入格式**：命中 query 下，傳給 Groq 的 messages 含 playbook system context，且內含來源標註 `檔名#標題` 與 section 內文。
- [ ] **兩套索引隔離**：playbook 檢索只回傳 playbook section；`search_knowledge_base`（術語 KB）檢索只回傳 `knowledge/*.md` section；互不出現對方的 heading。且 `search_knowledge_base` 既有測試不回歸。
- [ ] **observability**：`playbook_injection` log 於注入與 fallback 兩情境皆記錄且 `injected` 值正確；`_run_tool` 成功時記 name/args/回傳資訊（用 caplog 或等效驗證）。
- [ ] **debug CLI**：`retrieve_scored`（或 debug 進入點）對指定 query 回傳含分數的 top-k；可切換兩索引。
- [ ] **prompts 測試同步**：`tests/test_prompts.py` 中針對已 MOVE 條目的 substring 斷言（現有 `test_system_instruction_mentions_days_back_cap_and_long_period_mapping`、`test_system_instruction_maps_this_week_to_future_date_range`、`test_system_instruction_requires_earnings_hour_label_in_reply`）改為斷言在對應 playbook 檔案內容；保留在 `SYSTEM_INSTRUCTION` 的 STAY 條目仍可加/保留斷言。`test_prompt_constants_are_nonempty_strings` 續綠。
- [ ] **playbook 檔案健全性**：三檔各至少解析出 1 section；每個 section 含 觸發語/工具指引/回覆要求/禁止 四小節標記。
- [ ] 既有測試不回歸；測試離線、決定性、不打網路、不碰真實 AWS（沿用既有慣例）。

## Definition of done
- 全部 deliverable 打勾。
- `PYTHONPATH=src uv run pytest tests/ -v`（`make test`）全綠。
- chat_cli / local_server 實測可見 `playbook_injection` 與成功 tool 呼叫的 log（在 QA report 附證據）。
- evaluator 的 QA report 無 P0/P1。
