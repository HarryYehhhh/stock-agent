# Spec: News haystack 納入離線稽核（snapshot-audit Future work）

## Goal
把 production `NewsTable` dump 成 `snapshots/<date>/news.md`，納入 snapshot-audit judge 的檢索 haystack，讓新聞 / 市場摘要類的 bot 回答也能被資料驗證，消除 v1「新聞類問題因無 haystack 一律落 no-data / 誤判 ungrounded」的已知限制。

## Background
`docs/specs/snapshot-audit.md` 的 Future work 與 ADR-0010 §Consequences 皆明列：v1 haystack 只有 earnings（`snapshots/<date>/earnings.md`），故 stock-news / market-summary 型回答多落 `no-data`。本 sprint 落成該 future work 的第一項（news haystack），用同一 dump 機制產出 `news.md`。

judge 端本來就對整個 `snapshots/<date>/` 目錄非遞迴 glob 建索引（`retrieval._load_sections`），`news.md` 落地後**自然被納入同一混合索引**，無需改 judge 的檢索路徑。judge 端只需同步「已知限制」caveat 文字，並以正反例確認混合索引不互相稀釋。

沿用資產（不重寫）：
- audit 套件模式：`src/agent/audit/{paths,dump,judge}.py`、`python -m agent.audit <subcommand>`。
- boto3 全表 scan：`db/download_remote_store._scan_all`。
- 檢索核心：`knowledge/retrieval.retrieve_scored(query, top_k, source_dir)`（ADR-0008 stdlib-only BM25 + CJK bigram；ASCII 英文原樣可斷詞）。
- config：`agent.config.news_table()`（`NEWS_TABLE`，預設 `earnings-agent-news`）、`aws_region()`。

## 定位（沿用 ADR-0010 邊界）
- 純本機離線批次工具擴充，**不**改線上 runtime（`run_agent()` / prompts / tools / scheduler / Lambda / IaC）。
- 不新增檢索 / NLP 依賴（維持 ADR-0008 stdlib-only）。
- token 節約照舊（judge 空命中短路、`--limit`、temperature=0 不變）。

## User stories
- As a 維護者, I want 把 NewsTable dump 成帶日期、每 symbol/分類一 section 的 `news.md`, so that judge 能對「AAPL 最近有什麼新聞」類 Q/A 檢索到對應新聞而非一律 no-data。
- As a 維護者, I want news.md 與 earnings.md 共處同一 `snapshots/<date>/` 混合索引但不互相稀釋, so that 財報 Q/A 仍命中 earnings section、新聞 Q/A 命中 news section。
- As a 維護者, I want 快照如實反映 NewsTable 當下內容與其保留期限, so that judge 報告的 no-data 能被正確判讀成「快照缺料」而非「bot 幻覺」。

## NewsTable 實況（查證 `db/backend.py` + `infra/template.yaml`）
NewsTable（`earnings-agent-news`）PK=`headline`(S)，GSI `published-at-index`（HASH=`bucket`、RANGE=`published_at`），TTL 屬性 `ttl`。內含**兩種 bucket**：

| bucket | 寫入路徑 | 欄位 | TTL |
|---|---|---|---|
| `news`（一般新聞） | `DynamoDBBackend.insert_news`（`market_summary.refresh_news` → daily cron） | `headline`(PK)、`bucket`、`published_at`(ISO)、`category`（`big7`/`ai`）、`summary`、`symbol`（僅 `big7` 有）、`url`、`fetched_at`、`ttl` | `NEWS_TTL_SECONDS` = **14 天** |
| `market_summary`（市場摘要快取） | `DynamoDBBackend.insert_market_summary`（Groq-call 快取） | `headline`=`SUMMARY#<date>#<days_back>#<hash>`(PK)、`bucket`、`summary`（繁中摘要）、`news_ids`、`summary_date`、`days_back`、`news_ids_hash`、`created_at`、`ttl` | `MARKET_SUMMARY_TTL_SECONDS` = **2 天** |

**⚠️ 對需求原描述的修正（重要，影響 caveat）**：需求把「2 天 TTL」套到整個 NewsTable，但查證後：**一般新聞是 14 天 TTL**、只有 **market_summary 快取是 2 天 TTL**。故：
- 一般新聞 haystack 實際可涵蓋近 **~14 天**（daily cron 每天以 `days_back=1` 增補、headline 去重、14 天 TTL 到期才淘汰）——比原以為的「近 2 天」深很多，對 news Q/A grounding 是好消息。
- 市場摘要 haystack 只涵蓋近 **~2 天**（2 天 TTL），較淺。
- 兩者皆為「dump 當下 NewsTable 的內容」，非固定歷史；早於保留期的新聞已被 TTL 淘汰，dump 不到（→ 時間錯位 caveat，見下）。

## Design

### 1. dump 擴充：`snapshots/<date>/news.md`
- `--table` 新增 `news` 選項：`earnings|conversations|news|all`（`all` 含 news）。
- `_dump_news(date_str)`：boto3 **單次全表 scan** `news_table()`（`_scan_all`），在 Python 端依 `bucket` 分流：`news` → 一般新聞、`market_summary` → 市場摘要，其餘 bucket 忽略。
- 寫入 `snapshots/<date>/news.md`（與 earnings.md 同一 `snapshots/<date>/` 樹，隨即被 judge 索引）。
- 目錄不存在自動建立。

### 2. news.md 格式（相容 `retrieval.py` parser + 混索引不稀釋）
- 首個 `## ` 之前放 **metadata**（標題、產生時間、來源表、`news`/`market_summary` 各筆數、保留期說明）——parser 忽略首個 heading 前內容，人可讀但不進索引。
- **一般新聞 `big7`（有 symbol）**：每 symbol 一個 `## <SYMBOL> 新聞` section。body 逐則 `- <published_date> <headline>：<summary>`（英文原樣落地）。
- **一般新聞 `ai`（symbol=None）**：彙整成一個 `## AI 產業新聞` section，body 逐則 `- <published_date> <headline>：<summary>`。
- **市場摘要 `market_summary`**：彙整成一個 `## 市場摘要` section，body 逐則 `- <summary_date>（近 <days_back> 天）：<summary 截斷>`（繁中，CJK bigram 可斷詞）。
- **排序（diff 穩定）**：symbol section 以 symbol 字母序；每 section 內 `big7`/`ai` 以 `published_at` 降序（新到舊，貼合「最近」語意）、同日以 headline 字母序 tiebreak；市場摘要以 `summary_date` 降序。
- **section heading 帶類型詞（`... 新聞` / `市場摘要`）的理由——這是不稀釋的關鍵設計**：earnings.md 已用 `## <SYMBOL>`（純 symbol）。news 若也用純 `## <SYMBOL>` 會與 earnings 同 heading 撞（僅靠 body 區辨、較弱）。改用 `## <SYMBOL> 新聞`，heading 同時含 `<SYMBOL>` 與「新聞」token（heading 得 BM25 2x 權重）：
  - news Q/A（「AAPL 最近有什麼新聞」）query 含 `aapl`+`新聞`，兩者都 2x 命中 news heading，強勝只含 `aapl` heading 的 earnings.md#AAPL → 命中 news section。
  - earnings Q/A（「AAPL 財報 EPS 何時」）query 含 `eps`/日期 token，命中 earnings body、不含「新聞」→ 命中 earnings section。
  - 檢索來源 `Section.source` 為 `<stem>#<heading>`（`news#AAPL 新聞` vs `earnings#AAPL`），報告可辨別命中哪個檔。

### 3. judge 小改（只改 caveat 文字，不改檢索）
- **不改** judge 的檢索 / 配對 / 短路邏輯：`retrieve_scored(source_dir=snapshots/<date>/)` 本就 glob 目錄下全部 `*.md`，`news.md` 落地即自動納入同一混合索引。
- **只改** `_write_report` 的「已知限制」caveat 段，同步反映 haystack 現況：
  - haystack 現含 **earnings + news**（不再只有 earnings）。
  - 一般新聞涵蓋近 **~14 天**、市場摘要涵蓋近 **~2 天**（兩者為 dump 當下 NewsTable 內容）。
  - **時間錯位實例**：Q/A 讀自即時 DynamoDB，若該對話問的新聞早於快照保留期（新聞 >14 天、摘要 >2 天前）已被 TTL 淘汰，快照無此料 → 判 `no-data` 屬「快照缺料 / 時間錯位」，非 bot 幻覺，請搭配 verdict 細節與檢索來源判讀。
- judge 的「快照存在」前置檢查維持以 `earnings.md` 為 dump sentinel（`dump --table all` 會同時產出 earnings.md 與 news.md）；`news.md` 存在則自動索引、不存在不報錯（純加法）。

### 4. .gitignore
- 無需改動：`snapshots/` 已在 `.gitignore`（ADR-0010 D4），`news.md` 落在 `snapshots/<date>/` 下自然被忽略。

## Acceptance criteria
- [ ] `python -m agent.audit dump --table news`（或 `all`）從 NewsTable 產生 `snapshots/<date>/news.md`：首個 `## ` 前有 metadata；`big7` 每 symbol 一 `## <SYMBOL> 新聞` section、`ai` 一 `## AI 產業新聞` section、`market_summary` 一 `## 市場摘要` section；英文新聞原樣落地。
- [ ] news.md 排序穩定：symbol 字母序、section 內 `published_at` 降序（同日 headline tiebreak）、市場摘要 `summary_date` 降序。
- [ ] `retrieve_scored("<SYMBOL> 最近有什麼新聞", source_dir=snapshots/<date>/)` 命中該 symbol 的 `news#<SYMBOL> 新聞` section（earnings.md 同 symbol section 存在時不被其壓過）。
- [ ] **混索引不互相稀釋（正反例）**：同一 `snapshots/<date>/`（earnings.md + news.md）下——(正) 新聞 Q/A 首命中為 `news#...`；(反) 財報 Q/A（EPS / 財報日期）首命中為 `earnings#<SYMBOL>`。
- [ ] NewsTable 近期無資料時：news.md 仍產出（含 metadata + 「近期無資料」註記、無 section），不報錯（如實記錄）。
- [ ] judge 報告 caveat 更新：反映 haystack 現含 earnings + news、新聞 ~14 天 / 摘要 ~2 天、時間錯位判讀說明。
- [ ] `PYTHONPATH=src uv run pytest tests/ -v` 全綠（moto mock NewsTable + mock Groq）。
- [ ] 本機實測：真實 dump NewsTable 跑通（近兩天無資料時如實記錄、以 moto 補證據）；judge 對含新聞類 Q/A 的歷史對話（「最近有什麼AAPL新聞」「google最近有什麼新聞」）跑一輪——若新聞資料時段無法覆蓋歷史 Q/A，如實記錄為時間錯位 caveat 實例。

## Non-goals / Out of scope
- scheduler 寫入端新聞預篩選（另案）。
- 其他新資料源（company_summaries、watchlist 等）快照。
- 改動 judge 的檢索核心 / 配對 / 短路邏輯（僅改 caveat 文字；`retrieval.py` BM25 / 斷詞不動）。
- 改線上 bot 行為 / prompts / tools / scheduler / Lambda / IaC。
- embeddings / 向量 / 任何新第三方套件（維持 ADR-0008）。
- judge 讀快照而非 DynamoDB（仍是 ADR-0010 D2 的 Future work）。

## Open questions / 決策
- **N1（已決，section 切法）**：`big7` 按 symbol → `## <SYMBOL> 新聞`；`ai` → `## AI 產業新聞`；`market_summary` → `## 市場摘要`。理由：symbol section 對「AAPL 新聞」型 Q/A 有意義（heading 含 symbol，2x 權重），類型詞（新聞 / 市場摘要）進 heading 解決與 earnings.md 同 symbol 撞名的稀釋問題。
- **N2（已決，market_summary 納入且獨立 section）**：NewsTable 的 `market_summary` 快取雖只 2 天 TTL，但正是「市場摘要 / 大盤」型回答的 haystack，納入 `## 市場摘要`；淺 TTL 記為 caveat。（註：ADR-0010 D1 當時「market summaries 不做」的理由是 v1 控 scope，非「不可 dump」——本 sprint 正式納入。）
- **N3（已決，TTL / 涵蓋語意）**：修正需求原「2 天 TTL」描述——一般新聞 14 天、market_summary 2 天（見上表）。caveat 據實反映。
- **N4（已決，judge 前置檢查）**：維持以 `earnings.md` 為 dump sentinel，news.md 純加法（存在則索引、不存在不報錯），避免改動既有 judge 錯誤處理行為。
- **N5（交由 generator）**：news.md 內 `summary` / 市場摘要文字的截斷字元上限、metadata 具體欄位、`_dump_news` 的模組落點（併入 `dump.py`），由 generator 定，須：相容 parser（metadata 置首個 `## ` 前）、排序穩定、英文原樣。
