# Contract: news-haystack（NewsTable 納入離線稽核 haystack）

對應 spec: docs/specs/news-haystack.md
相關 ADR: docs/decisions/0011-news-haystack.md（延伸 ADR-0010）

## 前置
- 純本機離線 CLI 擴充，**不**改線上 runtime（`run_agent()` / prompts / tools / scheduler / Lambda / IaC）。
- 沿用既有：audit 套件模式（`src/agent/audit/{paths,dump,judge}.py`、`python -m agent.audit`）、`db/download_remote_store._scan_all` boto3 scan、`knowledge/retrieval.retrieve_scored(query, top_k, source_dir)`、`agent.config.news_table()` / `aws_region()`。
- 不新增第三方檢索 / NLP 依賴（ADR-0008 stdlib-only）。
- NewsTable 兩種 bucket 與 TTL 見 spec「NewsTable 實況」表：一般新聞 `bucket="news"`（14 天 TTL）、市場摘要 `bucket="market_summary"`（2 天 TTL）。

## Deliverables

### A. `dump` 子命令擴充（`agent/audit/dump.py`）
- [ ] `--table` 選項新增 `news`：`earnings|conversations|news|all`（`all` 含 news）。
- [ ] `_dump_news(date_str)`：boto3 **單次全表 scan** `news_table()` → 依 `bucket` 分流（`news` / `market_summary`，其餘忽略）→ 寫 `snapshots/<date>/news.md`。
- [ ] news.md 格式：
  - 首個 `## ` 之前為 metadata（標題、產生時間、來源表、`news`/`market_summary` 各筆數、保留期說明「一般新聞近 ~14 天、市場摘要近 ~2 天」）——parser 忽略此段。
  - `big7`（有 `symbol`）：每 symbol 一 `## <SYMBOL> 新聞` section；body 逐則 `- <published_at 日期> <headline>：<summary>`（英文原樣；summary 過長截斷）。
  - `ai`（`symbol` 為 None）：一 `## AI 產業新聞` section；body 逐則 `- <published_at 日期> <headline>：<summary>`。
  - `market_summary`：一 `## 市場摘要` section；body 逐則 `- <summary_date>（近 <days_back> 天）：<summary 截斷>`。
  - 排序：symbol 字母序；section 內 `published_at` 降序、同日 headline 字母序 tiebreak；市場摘要 `summary_date` 降序。
- [ ] NewsTable 空 / 近期無資料：仍寫 news.md（metadata + 「近期無資料」註記、無 section），不報錯。
- [ ] 目錄不存在自動建立。

### B. `judge` 子命令 caveat 更新（`agent/audit/judge.py`）
- [ ] **僅**更新 `_write_report` 的「已知限制」caveat 段（不改檢索 / 配對 / 短路 / prompt / JSON 解析）：
  - haystack 現含 **earnings + news**（取代 v1「僅含財報行事曆」文字）。
  - 一般新聞涵蓋近 ~14 天、市場摘要近 ~2 天（皆為 dump 當下 NewsTable 內容）。
  - 時間錯位：對話問的新聞若早於快照保留期（新聞 >14 天 / 摘要 >2 天）已被 TTL 淘汰、快照無此料 → 判 `no-data` 屬快照缺料，非 bot 幻覺。
- [ ] 前置檢查維持以 `snapshots/<date>/earnings.md` 為 dump sentinel；`news.md` 純加法（存在自動索引、不存在不報錯）。

### C. .gitignore
- [ ] 無需改動（`snapshots/` 已忽略，`news.md` 落於其下）。generator 確認即可，不新增規則。

## Out of scope
- scheduler 新聞預篩選（另案）。
- 其他新資料源快照（company_summaries / watchlist 等）。
- 改 judge 檢索核心 / 配對 / 短路邏輯、`retrieval.py` BM25 / 斷詞。
- 改線上 bot 行為 / prompts / tools / scheduler / Lambda / IaC。
- embeddings / 向量 / 新第三方套件。
- judge 讀快照而非 DynamoDB（仍為 Future work）。

## Tests required（moto mock NewsTable + mock Groq）
- [ ] dump news：seeded NewsTable（多 `big7` symbol + `ai` + `market_summary`，含英文 headline/summary、缺 `symbol`/`url` 的列）→ 斷言 news.md 有 metadata 段、`## <SYMBOL> 新聞` / `## AI 產業新聞` / `## 市場摘要` section、格式與排序（symbol 字母序、published_at 降序、summary_date 降序）正確、英文原樣落地。
- [ ] `bucket` 分流：`market_summary` 條目進 `## 市場摘要`、不混入一般新聞 section；未知 bucket 被忽略。
- [ ] 檢索命中：`retrieve_scored("<SYMBOL> 最近有什麼新聞", source_dir=snapshots/<date>/)` 首命中為 `news#<SYMBOL> 新聞`。
- [ ] **混索引不互相稀釋（正反例，同時放 earnings.md + news.md）**：
  - (正) 新聞型 query 首命中為 `news#...` section。
  - (反) 財報型 query（含 EPS / 財報日期）首命中為 `earnings#<SYMBOL>` section。
- [ ] NewsTable 空 → news.md 產出且含「近期無資料」註記、不報錯、無 section。
- [ ] judge caveat：`_write_report` 產出的 report 含更新後 caveat 文字（earnings + news、~14 天 / ~2 天、時間錯位）。
- [ ] `--table all` smoke：一次產出 earnings.md + conversations.md + news.md。
- [ ] 既有 audit 測試無 regression（judge 正常路徑 / 空命中短路 / `--limit` / 配對 等）。

## Definition of done
- 全部 deliverable 打勾。
- `PYTHONPATH=src uv run pytest tests/ -v` 全綠。
- 本機實測：`python -m agent.audit dump --table news`（或 `all`）對真實 NewsTable 跑通並產出 `snapshots/<date>/news.md`——近兩天無資料時如實記錄該情形，並以 moto seeded 測試補齊格式 / section / 排序證據；附 news.md 產出片段。
- 本機實測：`python -m agent.audit judge` 對含新聞類 Q/A 的歷史對話（如「最近有什麼AAPL新聞」「google最近有什麼新聞」）跑一輪，附一則 verdict 佐證；若新聞資料時段無法覆蓋歷史 Q/A，如實記錄為時間錯位 caveat 實例（no-data / ungrounded 屬快照缺料而非幻覺）。
- evaluator QA report 無 P0/P1。
