# Spec: News section 切細（symbol×日期）+ 修復 judge 取證截斷

## Goal
把 `snapshots/<date>/news.md` 的新聞 section 從「每 symbol 一大節」切細成「每 symbol×日期一節」，並調整 dump 摘要節錄長度與 judge 的 `MAX_SECTION_CHARS`，讓**任何被檢索命中的 news section 的完整證據都能進 judge prompt**，消除熱門 symbol 大量新聞被 `MAX_SECTION_CHARS=1000` 截斷、導致新聞類 Q/A 誤判 ungrounded 的問題。

## Background
`docs/qa-reports/news-haystack.md`（2026-07-27）確認的 🟡：news-haystack sprint 讓 `_dump_news` 每 symbol 產一個聚合 section（`## <SYMBOL> 新聞`），涵蓋近 14 天全部新聞。實測 `## AAPL 新聞` 單節達 **16,135 字元 / 65 則**。judge 的 `_ask_judge` 對每個命中 section 套用既有 `MAX_SECTION_CHARS = 1000` 截斷 → 只保留最新 ~4 則，較舊（但仍在 14 天保留窗口內）的證據根本沒送進 Groq prompt。QA 複驗確認 7/21「最近有什麼AAPL新聞」被判 ungrounded 的**真正原因是截斷、非時間錯位、非幻覺**：bot 引用的 5 則標題（日期 2026-07-20）都在快照的 `## AAPL 新聞` 節內，只是被截在 prompt 之外。

使用者已定案**方案 A：切細 section**。切細後單節字元量大幅下降（16k → 單日尖峰約 1.2-2k），配合 dump 摘要節錄縮短與適度調高 `MAX_SECTION_CHARS`，讓命中節的完整證據落入 prompt。

沿用資產（不重寫）：
- audit 套件：`src/agent/audit/{dump,judge}.py`、`python -m agent.audit dump|judge`。
- 檢索核心 `knowledge/retrieval.py`（BM25 + CJK bigram，ADR-0008，**不動**）；judge 檢索/配對/短路/prompt 結構/JSON 解析**不動**（唯 `MAX_SECTION_CHARS` 數值可調、`_write_report` caveat 文字可同步）。
- news-haystack 既定：單次 scan NewsTable 依 `bucket` 分流（`news` / `market_summary`）、metadata 置首個 `## ` 前、排序穩定、英文原樣落地。

## 定位（沿用 ADR-0010/0011 邊界）
- 純本機離線批次工具調整，**不**改線上 runtime（`run_agent()` / prompts / tools / scheduler / Lambda / IaC）。
- 不新增檢索 / NLP 依賴（維持 ADR-0008 stdlib-only）。
- judge 的檢索/配對/短路/prompt 結構不變；`top_k` 預設 3 不變。

## User stories
- As a 維護者, I want news.md 每 symbol×日期一節（`## <SYMBOL> 新聞 <YYYY-MM-DD>`）, so that judge 命中的節夠小、完整證據能進 prompt，不再因單一大節被截斷而漏掉仍在保留窗口內的舊證據。
- As a 維護者, I want 切細後單節 P95 字元量 ≤ judge 取證上限, so that 稽核不再把「證據被截斷」誤報成 bot 幻覺（ungrounded）。
- As a 維護者, I want 切細不讓「混索引不稀釋」正反例回歸, so that 新聞類 Q/A 仍首命中 news 節、財報類 Q/A 仍首命中 earnings 節。

## 字元分佈分析（依 QA report 真實數據點推算）
> 註：本 spec 撰寫環境無法直接跑 dump，以下用 `docs/qa-reports/news-haystack.md` 記載的真實實測值推算；generator 須以真實 dump 複核（見 contract DoD）。

真實錨點（news-haystack 實測）：
- `## AAPL 新聞` 單一聚合節 = **16,135 字元 / 65 則 / 近 14 天** → 每則行 ≈ **248 字元**（行格式 `- <YYYY-MM-DD> <headline>：<summary(≤200)>`，摘要現截斷上限 `_NEWS_SUMMARY_CHARS=200`）。
- AI 產業新聞 = **~3,500 字元 / 16 則**（≈ 219 字元/則，~1.1 則/日）。
- 一般新聞總計 321 則（7 個 big7 symbol + AI），market_summary 0 筆（2 天 TTL 當下無快取）。

切成 symbol×日期後（每則仍 ≈248 字元；AAPL 平均 65/14 ≈ 4.6 則/日，新聞會在事件日群聚，估尖峰日 6-10 則）：

| 粒度 | 每節字元估算 | 對 `MAX_SECTION_CHARS=1000` |
|---|---|---|
| AAPL 尖峰日 6 則（現 200 摘要） | ≈ 1,490 | 超過 → 仍被截 |
| AAPL 尖峰日 8 則（現 200 摘要） | ≈ 1,984 | 超過 |
| AAPL 尖峰日 10 則（現 200 摘要） | ≈ 2,480 | 超過 |
| AAPL 尖峰日 8 則（摘要縮 150） | ≈ 1,580 | 仍略超 1000 |
| AI 產業新聞（單日 1-3 則） | ≈ 220-660 | 未超 |
| 市場摘要（單日 ~1 則，摘要截斷） | ≈ 210 | 未超 |

結論：**只切 symbol×日期仍不足**——熱門 symbol 尖峰日單節（1.2-2.5k 字元）仍超 `MAX_SECTION_CHARS=1000`。需組合手段（見 Design 決策）。

## Design

### 1. dump section 切細（symbol×日期）
- **big7（有 `symbol`）**：每 symbol×日期一節，heading `## <SYMBOL> 新聞 <YYYY-MM-DD>`（`<YYYY-MM-DD>` 取 `published_at[:10]`；缺 `published_at` 的列歸入 `## <SYMBOL> 新聞 日期未知` 節）。body 逐則 `- <headline>：<summary>`（日期已在 heading，行內可省 date 前綴；由 generator 定，須排序穩定）。
- **AI（`symbol` 為 None）**：同樣按日期切，heading `## AI 產業新聞 <YYYY-MM-DD>`。理由：AI 節現況 3,500 字元 16 則，若維持單節、即使調高 `MAX_SECTION_CHARS` 到 2000 仍超（3.5k > 2k），會重演同一截斷 bug；按日期切後每節 ~220-660 字元，遠低於上限，且維持粒度一致。
- **market_summary**：按 `summary_date` 切，heading `## 市場摘要 <YYYY-MM-DD>`。理由：2 天 TTL 下至多 ~2 則、每節 ~210 字元，切細成本近零，粒度一致、diff 穩定。
- **排序（diff 穩定）**：節與節之間——先 symbol 字母序，同 symbol 內日期**降序**（新到舊）；AI 節接於所有 symbol 節之後、日期降序；市場摘要節最後、`summary_date` 降序。節內同日多則以 headline 字母序 tiebreak（沿用既有 `_sort_news_rows` 兩段 stable sort 精神）。
- **只為有資料的 (symbol,日期) / (日期) 產節**，不產空節。
- metadata（首個 `## ` 前）沿用既有欄位；可加一行說明「每 symbol×日期一節」（人可讀、不進索引）。

### 2. dump 摘要節錄縮短（手段 a）
- `_NEWS_SUMMARY_CHARS`：**200 → 150**。理由：headline 承載新聞身分（judge 靠 headline 字面與 bot 回答對齊），摘要 150 字元足夠提供 grounding 上下文；縮短後每則行 ≈248→~198 字元，AAPL 尖峰日 8 則 ≈1,984→~1,580、10 則 ≈2,480→~1,980，給上限留餘裕。
- 同步套用於 news 行與 market_summary 行（沿用同一 `_truncate_summary`）。

### 3. judge 取證上限調高（手段 b）
- `MAX_SECTION_CHARS`：**1000 → 2000**（`src/agent/audit/judge.py:40`，唯一數值改動；`_ask_judge` / 檢索 / 配對 / 短路 / prompt 結構 / `_extract_json` 皆不動）。
- **目標**：切細後 symbol×日期單節 P95 ≤ 取證上限（2000），使命中節完整進 prompt。切細（尖峰日 6-8 則 ≈1,190-1,580 字元，摘要 150）+ 上限 2000 → P95 舒適落在上限內；僅極端 >10 則/單日（P99）可能仍略超，記為殘留 caveat。
- **token 預算核算**（不得逼近 Groq TPM 12000）：
  - 命中片段 `top_k=3` × 2000 字元 = 6000 字元；英文為主新聞 ≈4 字元/token → ~1,500 token，保守混合 ≈3 字元/token → ~2,000 token。
  - 問題 ≤800 + 回答 ≤800 字元（`MAX_TEXT_CHARS=800` 不變，繁中 ~1.7 字元/token）→ ~940 token。
  - system prompt ~330 字元 → ~200 token。
  - 單次 judge 請求 input ≈ 2,650-3,150 token + output ~200 token ≈ **~3,400 token/請求 ≈ TPM 12000 的 ~28%**，未逼近上限。安全。
- `MAX_SECTION_CHARS` 為 generator 可微調參數：若真實 dump 的尖峰日分佈與推算不同，generator 得在 token 預算內微調（**上限 ≤ 2500**：3×2500 字元 ≈ 2,500 token、單請求 ≈ ~4,000 token，仍 < TPM 33%）與/或摘要字元數，binding 目標是「切細後 P95 節 ≤ 所選取證上限」，須以真實 dump 佐證。

### 4. report caveat 同步（`_write_report` 文字，允許）
- 反映修復後現況：news 節現為 symbol×日期粒度，命中節的完整證據會進 prompt（至取證上限），**移除/弱化** news-haystack 遺留的「大型 section 被截斷→漏證據」失效模式描述。
- 保留既有時間錯位（TTL 淘汰→缺料）caveat。
- 補殘留 caveat（誠實）：極端單日新聞量特大（單一 symbol×日期 > 取證上限）時，該日最舊數則仍可能被截；屬罕見尾端，非 TTL、非幻覺。

## Acceptance criteria
- [ ] `python -m agent.audit dump --table news`（或 `all`）產出的 news.md：big7 每 symbol×日期一節 `## <SYMBOL> 新聞 <YYYY-MM-DD>`、AI 每日期一節 `## AI 產業新聞 <YYYY-MM-DD>`、market_summary 每日期一節 `## 市場摘要 <YYYY-MM-DD>`；metadata 置首個 `## ` 前；英文原樣落地。
- [ ] 排序穩定：symbol 字母序、同 symbol 內日期降序、節內同日 headline 字母序 tiebreak；AI/市場摘要日期降序。
- [ ] `_NEWS_SUMMARY_CHARS = 150`；`MAX_SECTION_CHARS = 2000`（或 generator 在 token 預算 ≤2500 內微調後的值，附真實 dump P95 佐證）。
- [ ] **核心目標**：對真實 dump 快照，symbol×日期單節字元量 P95 ≤ 取證上限（切細後命中節完整進 judge prompt）。generator 以真實 dump 計算並記錄字元分佈（P50/P95/max）。
- [ ] **回歸 · 混索引不稀釋（正反例，不得回歸）**：同一 `snapshots/<date>/`（earnings.md + news.md）下——(正) 新聞型 query 首命中為 `news#<SYMBOL> 新聞 <date>` 節；(反) 財報型 query（EPS / 財報日期）首命中為 `earnings#<SYMBOL>` 節。既有 news-haystack 對舊 heading（`news#AAPL 新聞`）斷言的測試須更新為新粒度 source 字串。
- [ ] **回歸 · 上輪誤判案例**：7/21「最近有什麼AAPL新聞」引用的標題（日期 2026-07-20）若仍在 14 天保留窗口內 → 對應 `## AAPL 新聞 2026-07-20` 節完整進 judge prompt，judge 能看到證據（不再因截斷漏證）；若該日新聞已滾出 14 天窗口，如實記錄為時間錯位（非本次可控）。
- [ ] `PYTHONPATH=src uv run pytest tests/ -v` 全綠（moto mock NewsTable + mock Groq）。
- [ ] judge 報告 caveat 更新：反映 news 節現為 symbol×日期粒度、命中節完整進 prompt、殘留極端尾端截斷 caveat；保留 TTL 時間錯位 caveat。

## Non-goals / Out of scope
- **方案 B**：節內二段取證（在 judge 端把大節切段取證）——本 sprint 不做。
- scheduler 寫入端新聞預篩選（另案）。
- 改 judge 的檢索核心 / 配對 / 短路 / prompt 結構 / JSON 解析（僅 `MAX_SECTION_CHARS` 數值 + `_write_report` caveat 文字）。
- 改 `retrieval.py` BM25 / 斷詞 / `HEADING_WEIGHT`。
- 改 `top_k` 預設（維持 3）。
- 改線上 bot 行為 / prompts / tools / scheduler / Lambda / IaC。
- embeddings / 向量 / 新第三方套件（維持 ADR-0008）。

## Open questions / 決策
- **G1（已決，粒度）**：三個 bucket 一律切 symbol/類別×日期，heading 帶類型詞 + 日期（`## <SYMBOL> 新聞 <date>` / `## AI 產業新聞 <date>` / `## 市場摘要 <date>`）。理由：粒度一致、避免 AI 大節重演截斷 bug、diff 穩定；heading 仍含類型詞（保留 news-haystack 的降稀釋設計）。
- **G2（已決，組合手段）**：切細 + 摘要縮 150 + 上限升 2000 三者並用。單靠切細（尖峰日仍 >1000）或單靠升上限（AI 3.5k / 熱門尖峰日仍超）都不足；三者組合讓 P95 舒適落在上限內且 token 預算安全。
- **G3（已決，粒度副作用視為正面但不 gate）**：切細後 news 節變短，BM25 文件長度正規化不再壓低 news 節分數，QA 🟡 #2 指出的「真實規模短查詢被 earnings 壓過」預期改善；但 `retrieval.py` 不動，此改善不列為 hard acceptance，只要 moto 正反例不回歸即可。
- **G4（交由 generator）**：body 行內是否保留 date 前綴（日期已在 heading）、metadata 具體措辭、`MAX_SECTION_CHARS` 在 ≤2500 內的最終值（須附真實 dump P95 佐證），由 generator 定。
