# ADR-0011: News haystack 納入離線稽核（延伸 ADR-0010）

- **Date**: 2026-07-27
- **Status**: Accepted
- 相關：ADR-0010（離線快照稽核 + LLM-judge over BM25，本 ADR 為其明列 Future work 的落成）、ADR-0008（stdlib-only BM25 + CJK bigram 檢索核心，沿用不改）

## Context
ADR-0010 v1 的稽核 haystack 只含 earnings（`snapshots/<date>/earnings.md`），故 stock-news / market-summary 型 bot 回答多落 `no-data`（ADR-0010 §Consequences 明列為負面 trade-off、Future work）。本 ADR 決定把 production `NewsTable` 納入 haystack。

需決定：(1) NewsTable 怎麼 dump（兩種 bucket 如何處理）；(2) section 怎麼切、heading 怎麼命名，才對「AAPL 最近有什麼新聞」型 Q/A 有意義**且**不與既有 earnings section 互相稀釋；(3) judge 端要改多少；(4) 涵蓋期限 / TTL 的真實語意與其對判讀的影響。

## Decision
1. **同機制、同一 haystack 樹**：用既有 audit dump 機制產 `snapshots/<date>/news.md`，與 earnings.md 同處 `snapshots/<date>/`。judge 端的 `retrieve_scored(source_dir=snapshots/<date>/)` 本就非遞迴 glob 該目錄全部 `*.md`，news.md 落地即自動納入**同一混合索引**——judge 檢索 / 配對 / 短路邏輯**零改動**。
2. **單次 scan、依 bucket 分流**：`_dump_news` 對 NewsTable 單次 `_scan_all`，Python 端依 `bucket` 分流：`news`（一般新聞）與 `market_summary`（市場摘要快取），其餘忽略。market_summary 雖 2 天 TTL 淺，但正是市場摘要型回答的 haystack，一併納入。
3. **section 切法**：`big7`（有 symbol）每 symbol 一 `## <SYMBOL> 新聞`；`ai` 一 `## AI 產業新聞`；`market_summary` 一 `## 市場摘要`。metadata 置首個 `## ` 前（不進索引）。symbol 字母序、section 內 published_at / summary_date 降序（diff 穩定）。英文內容原樣落地（ASCII tokenizer 支援）、繁中摘要靠 CJK bigram。
4. **heading 帶類型詞解決稀釋**：earnings.md 用純 `## <SYMBOL>`；news 用 `## <SYMBOL> 新聞`，heading 同時含 symbol 與「新聞」token（BM25 heading 2x 權重）。news query 含 `symbol`+`新聞` 兩 token 2x 命中 news heading、勝過只含 symbol 的 earnings heading；earnings query 含 EPS / 日期 token 命中 earnings body。故混合索引不互相稀釋。
5. **judge 只改 caveat 文字**：`_write_report` 的「已知限制」段同步為 earnings + news、一般新聞 ~14 天 / 市場摘要 ~2 天、時間錯位（對話問的新聞早於保留期已被 TTL 淘汰 → no-data 屬快照缺料非幻覺）。

## Consequences
- 正面：新聞 / 市場摘要型回答可被 ground，消除 v1「新聞類一律 no-data」限制；judge 的 no-data 更能反映真實缺料而非 haystack 空。
- 正面：純加法——判改動集中在 dump（新增 `_dump_news`）與 judge caveat 文字，檢索核心與既有 audit 行為不動，回歸風險低。
- 正面：釐清 TTL 實況（一般新聞 14 天、非原以為的 2 天），haystack 對新聞 Q/A 深度優於預期。
- 負面 / trade-off：混合索引下 BM25 為未正規化分數，理論上仍可能有邊角稀釋——以「heading 帶類型詞」降風險，並以正反例驗收鎖住。
- 負面 / trade-off：market_summary 僅 2 天 TTL，市場摘要 haystack 淺，較早的摘要 Q/A 仍會 no-data——記為 caveat，屬時間錯位而非缺陷。
- 負面：judge 仍讀即時 DynamoDB Q/A、檢索日期快照（ADR-0010 D2 的時間錯位 trade-off 不變）。

## Alternatives considered
- **news 用純 `## <SYMBOL>` heading（與 earnings 同名）**：最簡單，但與 earnings.md 同 symbol section 撞名，僅靠 body 區辨、稀釋風險高。否決，改帶類型詞 heading。
- **news 獨立成第三棵樹（`news_snapshots/<date>/`）分開索引**：可完全隔離不稀釋，但 judge 要對兩個 source_dir 各跑一次檢索再合併、改動 judge 檢索路徑，違反「純加法、judge 零改動」。否決——混合索引 + heading 類型詞已足夠隔離。
- **market_summary 不納入（維持 ADR-0010 D1）**：省事，但市場摘要型回答就沒 haystack、繼續 no-data，與本 sprint goal 相悖。ADR-0010 D1 當時「不做」是 v1 控 scope，非不可 dump。否決，納入為 `## 市場摘要`。
- **date 分組 news section（而非 symbol/分類）**：對「今天有哪些新聞」型 date query 較直接，但 judge query 已含 bot 回答（列出具體 symbol），symbol/分類分組即可命中，且與 ADR-0010 對 earnings 的同一取捨一致。否決。
