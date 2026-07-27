# QA Report: news-section-granularity

- **Contract**: docs/contracts/news-section-granularity.md
- **Date**: 2026-07-27
- **Verdict**: ⚠️ Pass with issues

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| A. big7 每 symbol×日期一節，heading `## <SYMBOL> 新聞 <YYYY-MM-DD>` | ✅ | 讀碼確認 `_group_by_date` + `_sorted_date_keys`；真實快照 `AAPL 新聞 2026-07-26` 等 heading 正確 |
| A. 缺 `published_at` 歸「日期未知」節、排末 | ✅ | `_sorted_date_keys` 明確把 `_UNKNOWN_DATE` 排最後；新測試 `test_dump_news_format_sections_and_sort` 覆蓋（`AAPL 新聞 日期未知` 排在 `AAPL 新聞 2026-07-10` 之後） |
| A. AI 每日期一節 `## AI 產業新聞 <date>` | ✅ | 讀碼 + 真實快照確認 |
| A. market_summary 每日期一節 `## 市場摘要 <date>`，取 `summary_date` | ✅ | `_group_by_date(summary_items, date_key_field="summary_date")` |
| A. 排序：symbol asc、同 symbol 內日期 desc、AI 節接於後日期 desc、市場摘要最後日期 desc、節內同日 headline asc tiebreak | ✅ | 讀碼確認邏輯正確；測試斷言完整涵蓋七種情境 |
| A. 只為有資料的 (symbol,日期)/(日期) 產節 | ✅ | 迴圈只走 `date_groups` 中實際存在的 key |
| A. metadata 沿用既有欄位 + 可加說明行 | ✅ | 新增「每 symbol×日期一節」說明行，metadata 位置不變 |
| A. NewsTable 空 → news.md 仍寫「近期無資料」、不報錯、無 section | ✅ | `test_dump_news_empty_table_no_error` 通過 |
| A. `_NEWS_SUMMARY_CHARS` 200→150 | ✅ | 新測試 `test_dump_news_summary_truncated_at_150_chars` 驗證 150 截斷 |
| B. `MAX_SECTION_CHARS` 調整，唯一數值改動，`_ask_judge`/檢索/配對/短路/`_extract_json`/prompt 結構不動 | ✅ | 選值 2500（授權上限），`git diff` 確認 `_ask_judge`/`_judge_pair`/`retrieve_scored` 呼叫/`_extract_json` 零改動 |
| B. 上限 ≤2500 且附真實 P95 佐證選值合理 | ⚠️ | 選 2500 在授權範圍內、佐證數字真實（見下方獨立複算）；但真實 P95=4565 **超過**所選上限，佐證「選值合理」而非「達成 P95≤上限」——見 🟡 Findings |
| C. caveat 反映 symbol×日期粒度、命中節完整進 prompt、弱化舊截斷描述 | ✅ | `_write_report` 新增對應段落，措辭準確 |
| C. 保留 TTL 時間錯位 caveat | ✅ | 原段落保留、僅補充 ADR-0012 引用 |
| C. 補殘留尾端 caveat，誠實（非 TTL、非幻覺） | ✅ | 措辭誠實，甚至比 spec 建議的「屬罕見尾端」更準確地反映實測頻率（見下方 Findings） |
| Out of scope 遵守（不改檢索核心/`retrieval.py`/`top_k`/線上 runtime） | ✅ | `git diff --stat` 確認僅動 `dump.py`/`judge.py`/tests/CHANGELOG；`retrieval.py` working tree clean |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 結果：430 passed, 0 failed, 0 skipped（與 generator 自報一致）
- 失敗測試：無
- 既有 suite regression：✅ 無（首次在 sandbox 內跑因 `ThreadingHTTPServer` bind 被 sandbox 擋下 13 個 `test_local_integration.py` 案例產生 `PermissionError`；這是 sandbox socket 限制，非程式碼問題——用 `dangerouslyDisableSandbox` 重跑後全綠 430/430）

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）

- **`src/agent/audit/judge.py:47`（MAX_SECTION_CHARS=2500）／DoD 核心目標未完全達成**：獨立對真實快照（`snapshots/2026-07-27/news.md`，`一般新聞筆數：321`，涵蓋 2026-07-24 財報週尖峰）重算字元分佈，用 production `parse_sections`：
  ```
  n=46, P50=1062, P95=4565, max=5373
  7/46 sections (15%) exceed MAX_SECTION_CHARS=2500:
    5373  AAPL 新聞 2026-07-24
    4826  MSFT 新聞 2026-07-24
    4565  NVDA 新聞 2026-07-24
    3456  NVDA 新聞 2026-07-25
    3289  GOOGL 新聞 2026-07-24
    2737  AAPL 新聞 2026-07-25
    2574  META 新聞 2026-07-24
  ```
  數字與 generator 自報的 P50=1062/P95=4565/max=5373 完全一致（獨立複算確認無誤植）。但 contract DoD 明文「佐證 **P95 ≤ 取證上限**」——即便取契約授權的最大值 2500，真實 P95（4565）仍近乎 2 倍超過，且不是單一 P99 極端值：7/46（15%）節在這次財報週尖峰即超過上限，集中在 7/24 同週多檔大型股（AAPL/MSFT/NVDA/GOOGL/META）同日報財報。這與 spec 原先「6-10 則/尖峰日、屬罕見尾端」的估算落差明顯（實測部分節 20+ 則）。
  generator 已誠實面對：選了契約授權的最大值（而非停在 spec 預設 2000）、如實記錄真實分佈、並把 `_write_report` caveat 從 spec 建議的「罕見尾端」改寫為「較常觸發此情況（非僅單一極端案例）」，沒有掩蓋落差。這不算實作缺陷（contract 明文禁止超過 2500，generator 不能片面突破邊界），而是 spec/ADR 設計期的容量估算與真實規模有落差，由本次真實 dump 佐證揭露。
  **建議**：財報週同日多檔大型股齊發財報是每季會重演的規律模式（非一次性異常），建議開 follow-up 處理，二擇一：
  1. **重算並提高上限**：目前 token 預算分析是以 2500 字元的 worst case 抓 ~4,000 token/請求；若把上限拉到能覆蓋 max=5373（例如抓 5500-6000），worst case `top_k=3 × 6000 字元 ≈ 18,000 字元`，用保守 3 字元/token 估算 ≈ 6,000 token + 問答/system ~1,140 token ≈ **~7,140 token/請求（TPM 12000 的 ~60%）**，仍有安全餘裕（本 QA 獨立試算，未逼近 TPM）——這是相對低成本的下一步。
  2. **方案 B（節內二段取證）**：ADR-0012 已記為 out of scope 但列為「未來可選」，若要徹底消除任何尖峰日仍被截斷的可能，需啟動此更複雜的方案。
  兩者皆非本 sprint 範圍（contract 明文 out of scope），但建議下一個 sprint 排入，且最好在下次財報季（約 3 個月後）前處理完畢。

### 🟢 Suggestions

- **`docs/contracts/news-section-granularity.md` DoD「token 預算複核」**：contract 要求「記錄一次真實 judge 請求的 input token（Groq usage log）」，但目前 `audit/2026-07-27/report.md` 與已知產物中未見任何持久化的 token 用量紀錄（`judge.py:178` 只用 `log.info` 印，未落檔）。本 QA 對真實捕獲的那筆請求（META Q/A，命中 `earnings#META` 47 字元 + `news#META 新聞 2026-07-25` 251 字元 + `news#META 新聞 2026-07-21` 410 字元）獨立試算 prompt 總字元數約 1,073、粗估 ~360 token，遠低於預算；worst case（3 節皆頂到 2500 字元上限）粗估約 ~2,850 token，仍遠低於 TPM 12000。數字上安全無虞，但下次若要嚴謹留痕，建議 `_ask_judge` 把 usage 一併寫進 `audit/<date>/report.md` 或另存 log 檔，而非僅印到 stdout/logger（不影響本次驗收結論，純建議）。

### ✅ What looks good

- **切分/排序邏輯乾淨可讀**：`_group_by_date` / `_sorted_date_keys` / `_sort_rows_by_headline` 三個小函式各司其職，取代原本耦合在 `_sort_news_rows` 裡的兩段 stable sort，可讀性優於改前。
- **7/21 AAPL grounded 回歸案例，獨立複驗通過**：不經 Groq（避免額外消耗共用 free-tier 額度），改用讀碼 + 真實 DynamoDB conversations 表 + 真實檢索層驗證：
  - 從真實 `earnings-agent-conversations` 表撈出 2026-07-21 08:03:14 UTC 的原始 Q/A（chat_id=2143054523，問題「最近有什麼AAPL新聞」）。
  - 對真實 2026-07-27 快照跑 `retrieve_scored(question+answer, top_k=3)`：首命中 `news#AAPL 新聞 2026-07-20`（score 55.776，content 長度 1494 字元，**未觸發 2500 字元截斷**）。
  - 逐條核對：assistant 回答引用的 5 則標題（XLK 投資者雙倍費用、RISE 新興市場、收藏品泡沫、蘋果 AI 優勢、蘋果告 OpenAI）在該節內**全數逐字對得上**（含截斷標記處理），無遺漏。
  - 結論：切細 + 上限調高後，該節完整、未截斷地落入 judge prompt，且引用證據齊全——與 generator 自報「ungrounded → grounded」的判定高度吻合（分數數值因兩次 dump 時間點不同、真實 NewsTable 資料量變化而有差異，屬預期的活資料波動，不影響結論）。
- **G3（NVDA 短查詢首命中 earnings# 波動）獨立複驗**：對真實快照跑 `"NVDA 最近有什麼新聞"` 確認首命中確為 `earnings#NVDA`（6.919）> `news#NVDA 新聞 2026-07-21`（5.223），與 generator 觀察一致；ADR-0012 G3 明文「不列為 hard acceptance，只要 moto 正反例不回歸即可」——確認屬實（ADR-0012 :98）。且對實際 judge 查詢模式（question+answer 全文，非短查詢）驗證無影響：真實 7/21 AAPL 案例（`"最近有什麼AAPL新聞" + 完整回答`）首命中正確為 `news#AAPL 新聞 2026-07-20`，證實 judge 實際運作路徑不受此短查詢波動影響。
- **caveat 文字誠實升級**：`_write_report` 新增的殘留 caveat 沒有照抄 spec 草稿的「罕見尾端」措辭，而是依真實資料改寫為「財報週熱門 symbol...較常觸發此情況（非僅單一極端案例）」，準確反映本 QA 獨立複算出的 15% 觸發率，是誠實且對維護者更有用的訊息。
- **邊界遵守**：`retrieval.py`／judge 檢索核心／`_ask_judge` prompt 結構／`_extract_json`／`top_k` 預設／線上 runtime（`run_agent`/prompts/tools/scheduler/Lambda/IaC）全數零改動，`git diff --stat` 確認改動範圍精準對應 contract 邊界。
- **測試新增精準對應 contract 要求**：`test_dump_news_section_stays_within_max_section_chars_at_peak_volume` 直接用 production `judge.MAX_SECTION_CHARS` 做斷言（而非硬編數字），未來若上限再調整，測試會自動跟著驗證正確目標，不會变成假綠燈。
- **無密鑰/測試產物混入**：`git status --ignored` 確認 `snapshots/` 與 `audit/` 真實實測產物皆被 `.gitignore` 排除，未混入待 commit 變更；diff 內容無 hardcode 憑證。

## 結論

- 是否可合併：**是**（無 🔴 Critical，contract deliverables 全數打勾，測試全綠，回歸案例獨立複驗通過）
- 最該先處理的 1–3 件事：
  1. **開 follow-up sprint 處理財報週尖峰截斷殘留**：真實 P95=4565、15% 節超過 2500 上限、且集中在會每季重演的多檔大型股同日財報週——建議下一步先評估「重算 token 預算 + 進一步提高上限（估可安全拉到 ~5500-6000）」這個低成本選項，不夠再考慮方案 B。
  2.（次要）若要嚴謹留痕 token 用量，`_ask_judge` 的 Groq usage 建議落檔而非只印 log，方便之後稽核佐證不必重新試算。
