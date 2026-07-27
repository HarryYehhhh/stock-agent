# Contract: news-section-granularity（news section 切細 symbol×日期 + 修復 judge 取證截斷）

對應 spec: docs/specs/news-section-granularity.md
相關 ADR: docs/decisions/0012-news-section-date-granularity.md（延伸 / 部分取代 ADR-0011 D3、D4）
上游 QA: docs/qa-reports/news-haystack.md（🟡：`MAX_SECTION_CHARS=1000` 截斷大型 news 節致誤判 ungrounded）

## 前置
- 純本機離線 CLI 調整，**不**改線上 runtime（`run_agent()` / prompts / tools / scheduler / Lambda / IaC）。
- 沿用既有：`src/agent/audit/{dump,judge}.py`、`db/download_remote_store._scan_all`、`knowledge/retrieval.retrieve_scored`、`agent.config.news_table()`。
- 不新增第三方依賴（ADR-0008 stdlib-only）。
- **judge 檢索 / 配對 / 短路 / prompt 結構 / `_extract_json` / `top_k` 預設 3 皆不動**；唯 `MAX_SECTION_CHARS` 數值可調、`_write_report` caveat 文字可同步。
- `retrieval.py`（BM25 / 斷詞 / `HEADING_WEIGHT`）不動。

## Deliverables

### A. `_dump_news` section 切細（`src/agent/audit/dump.py`）
- [ ] big7（有 `symbol`）：每 **symbol×日期** 一節，heading `## <SYMBOL> 新聞 <YYYY-MM-DD>`（日期取 `published_at[:10]`；缺 `published_at` 的列歸 `## <SYMBOL> 新聞 日期未知` 節）。
- [ ] AI（`symbol` 為 None）：每 **日期** 一節，heading `## AI 產業新聞 <YYYY-MM-DD>`。
- [ ] market_summary：每 **日期** 一節，heading `## 市場摘要 <YYYY-MM-DD>`（日期取 `summary_date`）。
- [ ] 排序：節間先 symbol 字母序、同 symbol 內日期降序；AI 節接於所有 symbol 節後、日期降序；市場摘要節最後、`summary_date` 降序。節內同日多則 headline 字母序 tiebreak。
- [ ] body 逐則格式由 generator 定（日期已在 heading，可省行內 date 前綴），須排序穩定、英文原樣落地。
- [ ] 只為有資料的 (symbol,日期)/(日期) 產節，不產空節。
- [ ] metadata（首個 `## ` 前）沿用既有欄位、parser 忽略；可加一行「每 symbol×日期一節」說明。
- [ ] NewsTable 空 / 近期無資料：仍寫 news.md（metadata + 「近期無資料」註記、無 section），不報錯。
- [ ] `_NEWS_SUMMARY_CHARS`：200 → **150**（news 行與 market_summary 行同步）。

### B. judge 取證上限調高（`src/agent/audit/judge.py`）
- [ ] `MAX_SECTION_CHARS`：1000 → **2000**（唯一數值改動）。若真實 dump 分佈需要，可在 token 預算內微調（**上限 ≤ 2500**），須附真實 dump P95 佐證選值合理。
- [ ] **不改** `_ask_judge` 結構、`retrieve_scored` 呼叫、空命中短路、`_extract_json`、`MAX_TEXT_CHARS`、prompt 文字（`_JUDGE_SYSTEM_PROMPT`）。

### C. report caveat 同步（`src/agent/audit/judge.py` `_write_report`，文字）
- [ ] 反映修復後現況：news 節現為 symbol×日期粒度、命中節完整證據進 prompt（至取證上限）；弱化/移除 news-haystack 遺留的「大型 section 截斷漏證」描述。
- [ ] 保留既有 TTL 時間錯位 caveat（新聞 >14 天 / 摘要 >2 天已被淘汰 → no-data 屬缺料）。
- [ ] 補殘留 caveat：極端單日新聞量特大（單一 symbol×日期 > 取證上限）該日最舊數則仍可能被截，屬罕見尾端、非 TTL、非幻覺。

## Out of scope
- 方案 B（judge 端節內二段取證 / 大節切段）。
- scheduler 新聞預篩選。
- 改 judge 檢索核心 / 配對 / 短路 / prompt 結構 / JSON 解析、`retrieval.py` BM25 / 斷詞 / `HEADING_WEIGHT`、`top_k` 預設。
- 改線上 bot / prompts / tools / scheduler / Lambda / IaC；embeddings / 新第三方套件。

## Tests required（moto mock NewsTable + mock Groq）
- [ ] **切細格式 + 排序**：seeded NewsTable（同一 symbol 跨多日期 + 同日多則 + AI 跨日期 + market_summary 跨日期 + 缺 `symbol`/`published_at` 的列）→ 斷言 heading 為 `## <SYMBOL> 新聞 <date>` / `## AI 產業新聞 <date>` / `## 市場摘要 <date>`；節間 symbol 字母序 + 日期降序；節內同日 headline 字母序；缺 `published_at` 歸「日期未知」節；英文原樣。
- [ ] **摘要截斷**：>150 字元 summary 被截到 150（含截斷標記），≤150 原樣。
- [ ] **切細後單節不再過大（核心目標的單元佐證）**：以貼近真實規模的 seeded 資料（單一 symbol 單日多則，模擬尖峰），斷言該 symbol×日期節 `Section.content` 長度 ≤ `MAX_SECTION_CHARS`（即命中即完整進 prompt，無截斷）。可直接用 production `retrieval.parse_sections` 取 content 長度斷言。
- [ ] **檢索命中（切細 source 字串）**：`retrieve_scored("<SYMBOL> 最近有什麼新聞", source_dir=snapshots/<date>/)` 首命中為某 `news#<SYMBOL> 新聞 <date>` 節（非 `earnings#<SYMBOL>`）。
- [ ] **混索引不稀釋正反例（不得回歸，同放 earnings.md + news.md）**：(正) 新聞型 query 首命中 `news#...` 節；(反) 財報型 query（EPS / 財報日期）首命中 `earnings#<SYMBOL>`。更新既有 news-haystack 對舊 heading（`news#AAPL 新聞`）斷言的測試為新粒度 source。
- [ ] NewsTable 空 → news.md 產出含「近期無資料」註記、不報錯、無 section。
- [ ] `bucket` 分流：market_summary 進 `## 市場摘要 <date>`、不混入一般新聞節；未知 bucket 忽略。
- [ ] judge caveat：`_write_report` 產出含更新後 caveat（symbol×日期粒度、命中節完整進 prompt、殘留尾端截斷、保留 TTL 時間錯位）。
- [ ] `--table all` smoke：一次產出 earnings.md + conversations.md + news.md。
- [ ] 既有 audit 測試無 regression（judge 正常路徑 / 空命中短路 / `--limit` / 配對；dump earnings / conversations）。

## Definition of done
- 全部 deliverable 打勾。
- `PYTHONPATH=src uv run pytest tests/ -v` 全綠。
- **本機真實 dump 佐證核心目標**：`python -m agent.audit dump --table news`（或 `all`）對真實 NewsTable 跑通，計算並記錄切細後 symbol×日期單節字元分佈（P50 / P95 / max 與筆數），佐證 **P95 ≤ 取證上限**；附熱門 symbol（如 AAPL）尖峰日單節片段。（若近兩天/當下 NewsTable 資料稀疏無法呈現尖峰，如實記錄，並以「切細後單節不再過大」單元測試 + QA report 的 16,135 字元錨點推算補證。）
- **本機真實 judge 佐證回歸案例**：`python -m agent.audit judge` 對含新聞類 Q/A 的歷史對話跑一輪；特別複核 7/21「最近有什麼AAPL新聞」——若 2026-07-20 AAPL 新聞仍在 14 天窗口內，確認 `## AAPL 新聞 2026-07-20` 節完整進 judge prompt、judge 看得到引用標題證據（相較 news-haystack 的截斷誤判有改善）；若已滾出窗口，如實記錄為時間錯位。
- **token 預算複核**：記錄一次真實 judge 請求的 input token（Groq usage log），確認未逼近 TPM 12000（預期 ~3-4k token/請求）。
- report caveat 落地確認（真實 `audit/<date>/report.md` 含更新文字）。
- evaluator QA report 無 P0/P1。
