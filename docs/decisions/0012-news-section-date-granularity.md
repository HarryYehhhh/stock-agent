# ADR-0012: News haystack 切至 symbol×日期粒度並調高 judge 取證上限

- **Date**: 2026-07-27
- **Status**: Accepted
- 相關：**部分取代 ADR-0011 的 Decision 3（section 切法）與 Decision 4（heading 帶類型詞降稀釋）**；延伸 ADR-0010（離線快照稽核 + LLM-judge over BM25）；沿用 ADR-0008（stdlib-only BM25 + CJK bigram，不改）。

## Context
ADR-0011 讓 `_dump_news` 每 symbol 產一個聚合 section（`## <SYMBOL> 新聞`），涵蓋近 14 天全部新聞。news-haystack QA（`docs/qa-reports/news-haystack.md`）以真實資料複驗，暴露兩個交互問題：

1. **取證截斷（主問題）**：`## AAPL 新聞` 單節實測 **16,135 字元 / 65 則**。judge 的 `_ask_judge` 對每個命中節套用 `MAX_SECTION_CHARS = 1000` 截斷，只留最新 ~4 則；較舊（但仍在 14 天保留窗口內）的證據沒進 Groq prompt，導致 7/21「最近有什麼AAPL新聞」被誤判 `ungrounded`——實際引用的 5 則標題（2026-07-20）都在節內、只是被截在 prompt 之外。此為「每 symbol 單一大節」設計（本 sprint 前 earnings.md 每 symbol 僅 2-3 行、從未觸發截斷路徑）新暴露的失效模式。
2. **真實規模稀釋**：ADR-0011 Decision 4 主張「heading 帶類型詞使 news query 2x 命中、強勝 earnings」，但真實規模下 7 個 big7 有 6 個短查詢首命中仍是 `earnings#<SYMBOL>`——因大型 news 節被 BM25 文件長度正規化壓低分數，抵銷 heading 2x 優勢。judge 實際查詢（question+answer，含具體標題）仍能消歧、線上稽核未受影響，但 ADR-0011 記為「理論上」的 trade-off 與真實數據不符。

使用者定案**方案 A：切細 section**。需決定：(1) news section 切到什麼粒度；(2) AI / market_summary 是否同切；(3) 如何確保命中節完整證據進 prompt（截斷上限如何取，且不逼近 Groq TPM）。

## Decision
1. **section 切至 symbol×日期粒度**：big7 `## <SYMBOL> 新聞 <YYYY-MM-DD>`、AI `## AI 產業新聞 <YYYY-MM-DD>`、market_summary `## 市場摘要 <YYYY-MM-DD>`。取代 ADR-0011 D3 的「每 symbol / 每類別單一聚合節」。三個 bucket 一律切日期，粒度一致。heading 保留類型詞（`新聞` / `AI 產業新聞` / `市場摘要`），沿用 ADR-0011 D4 的降稀釋設計。
2. **組合手段確保完整取證**：(a) dump 摘要節錄 `_NEWS_SUMMARY_CHARS` 200→150；(b) judge `MAX_SECTION_CHARS` 1000→2000。切細後熱門 symbol 尖峰日單節（估 6-8 則 ≈1,190-1,580 字元，摘要 150）P95 舒適落在 2000 內，命中節完整進 prompt。單靠切細（尖峰日仍 >1000）或單靠升上限（AI 3.5k 單節、熱門尖峰日仍超）都不足，故三者並用。
3. **token 預算約束**：`MAX_SECTION_CHARS=2000` 下，`top_k=3` 命中片段 3×2000 字元 ≈ 1,500-2,000 token + 問答 ≤1,600 字元 ≈ 940 token + system ~200 token ≈ **~3,400 token/judge 請求 ≈ TPM 12000 的 ~28%**，未逼近上限。上限為 generator 可微調參數（≤2500，單請求 ≤ ~4,000 token 仍安全），binding 目標是「切細後 P95 節 ≤ 所選取證上限」並以真實 dump 佐證。
4. **judge 檢索核心不動**：`retrieve_scored` / 配對 / 短路 / prompt 結構 / `_extract_json` / `top_k` 預設 3 皆零改動；只調 `MAX_SECTION_CHARS` 數值與 `_write_report` caveat 文字。切細後命中的 symbol×日期節即完整落入 prompt，直接解決主問題。

## Consequences
- 正面（主目標）：命中節的完整證據進 judge prompt，消除「大型 news 節被截斷→漏仍在保留窗口內的舊證據→誤判 ungrounded」失效模式；7/21 AAPL 誤判類案例在切細後可正確看到證據。
- 正面（副作用）：news 節變短，BM25 文件長度正規化不再壓低其分數，ADR-0011 QA 🟡 觀察到的「真實規模短查詢被 earnings 節壓過」預期改善（但 `retrieval.py` 不動，不列為 hard gate）。
- 正面：粒度一致（三 bucket 皆 symbol/類別×日期），AI 大節（3.5k）不再重演截斷 bug；diff 穩定（節按 symbol/日期穩定排序）。
- 負面 / trade-off：section 數量大增（每 symbol × 每有新聞的日期一節），BM25 索引 doc 數變多、avg_len 下降——屬離線工具、規模小（數百節），無效能疑慮。
- 負面 / trade-off：dump 摘要縮至 150 字元，人工 review 時每則摘要上下文更短——headline 完整保留、grounding 主要靠 headline 對齊，可接受。
- 負面 / 殘留：極端單日新聞量特大（單一 symbol×日期 > 取證上限 2000）時，該日最舊數則仍會被截——屬罕見尾端（P99），記為 report caveat；非 TTL、非幻覺。方案 B（節內二段取證）為未採用的更徹底解，本次不做。
- 中性：時間錯位（judge 讀即時 DynamoDB、檢索日期快照；TTL 淘汰→缺料）trade-off 不變（ADR-0010 D2 / ADR-0011），caveat 保留。

## Alternatives considered
- **只調高 `MAX_SECTION_CHARS`、不切細**：最省事，但要涵蓋 16k 的 AAPL 單節需把上限拉到 ~16000，`top_k=3` 即 ~48000 字元 ≈ 16k token，直接逼近/超過 TPM 12000。否決。
- **方案 B：judge 端節內二段取證（保留大節、檢索時對節內再切段取最相關片段）**：不改 dump 格式、能精準取證，但需改 judge 檢索/取證核心（違反「judge 核心不動」邊界），複雜度高。本 sprint 明確 out of scope，列為未來可選。否決（本 sprint）。
- **只切 symbol×日期、不動 `MAX_SECTION_CHARS` / 摘要**：切細後尖峰日單節仍 1.2-2.5k > 1000，AI 3.5k 單節（若不同切）仍超，部分節仍被截，未達「P95 ≤ 上限」目標。否決，改組合手段。
- **AI / market_summary 維持單一聚合節（只切 big7）**：AI 3.5k 單節即使升上限到 2000 仍超、重演截斷 bug；粒度不一致。否決，三 bucket 一律切日期。
- **news 用純日期 heading（`## <date>`，跨 symbol 聚合當日全部新聞）**：對「今天有哪些新聞」型 date query 直接，但跨 symbol 聚合又會脹大單節、且丟失 symbol heading token（稀釋 symbol 新聞 query 命中）。否決，採 symbol×日期。
