# QA Report: calendar-selection-and-log-fix

- **Contract**: 無（本次為需求導向修正，非 sprint contract；來源：`make chat` 實測「本週有什麼科技大廠公布財報」發現的 3 個問題）
- **Date**: 2026-07-27
- **Verdict**: ✅ Pass

## 需求 checklist
| 項目 | Status | Note |
|---|---|---|
| 1. chat_cli 靜音 httpx INFO log（避免洩漏 Finnhub token） | ✅ | `chat_cli.py:25` `logging.getLogger("httpx").setLevel(logging.WARNING)`；獨立確認 `_http.py` 無自建 log、httpcore 只在 DEBUG 記錄連線細節（basicConfig 為 INFO 本就不會印出），無其他洩漏路徑 |
| 2. `get_earnings_calendar` 截斷改「日期覆蓋 + 規模優先」 | ✅ | `earnings.py:52-83` `_select_rows`：按 date 分組→組內 `(revenueEstimate is None, -revenueEstimate)` 降冪排序→日期字典序 round-robin 取滿 limit；套用在白名單過濾之前，upsert 與回傳仍一致 |
| 3. `earnings.md` 補「科技大廠與大型科技股財報查詢」section | ✅ | 觸發語/工具指引/回覆要求/禁止四段齊全，Big 7 清單（AAPL/MSFT/GOOGL/AMZN/META/NVDA/TSLA）正確、明確禁止 FB 舊代碼 |
| 兩個 query 各自穩定命中正確 section | ✅ | 獨立呼叫 production 注入函式 `_retrieve_playbook_hits`（非只信 generator 數字）驗證，見下方「獨立重測」 |

## Test results
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙箱內結果：364 passed / 13 errors（`tests/test_local_integration.py` 起本地 `ThreadingHTTPServer` 需要 bind socket，被 sandbox 擋下 `PermissionError: Operation not permitted`，非程式碼問題）
- 停用 sandbox 重跑：**377 passed**，與 generator 自報數字一致
- 既有 suite regression：✅ 無

## 獨立重測（不採信 generator 自報數字）

用 `retrieve_scored` 直接跑兩個 query（`source_dir=PLAYBOOKS_DIR`, `top_k=5`）：

```
本週有什麼公司發布財報
  10.819  earnings#財報行事曆時間查詢
   3.489  stock-news#個股新聞查詢與摘要整理
   2.354  earnings#特定個股財報與分析師預期查詢
   2.216  earnings#科技大廠與大型科技股財報查詢

本週有什麼科技大廠公布財報
   9.919  earnings#科技大廠與大型科技股財報查詢
   9.296  earnings#財報行事曆時間查詢
   2.842  stock-news#個股新聞查詢與摘要整理
   1.191  earnings#特定個股財報與分析師預期查詢
```

進一步呼叫 `agent.py` 實際使用的 `_retrieve_playbook_hits()`（`PLAYBOOK_TOP_K=2` + `MIN_SCORE=3.5` 皆為既有、非本次改動的校準值）確認**實際會被注入 system context 的內容**：

```
本週有什麼公司發布財報 → 注入：earnings#財報行事曆時間查詢 (10.819)
本週有什麼科技大廠公布財報 → 注入：earnings#科技大廠與大型科技股財報查詢 (9.919)
                            + earnings#財報行事曆時間查詢 (9.296)
```

第一個 query 只注入行事曆 section（stock-news 3.489 未達 3.5 門檻，被濾掉，符合預期，不算誤命中）；第二個 query 新 section 排第一、行事曆 section 排第二一起注入——兩者內容無矛盾：新 section 明確說「這類問題不要只依賴全市場財報行事曆」並指引改用 `symbol` 逐一查詢，行事曆 section 的通用查詢區間規則（未來視角、`hour` 標示）仍可被沿用，不會讓模型無所適從。與 generator 自報數字一致。

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）
無

### 🟢 Suggestions
- `earnings.py:66` `_select_rows` 排序 key 用 `-(it.get("revenueEstimate") or 0)`：因為型別分支已用 `is None` 擋在 tuple 第一個元素，`or 0` 只在值恰好為 `0`（真實 0 元預期營收）時才會生效，行為等價於 `0`，不影響排序正確性；純粹是可讀性上可以寫成 `-(it.get("revenueEstimate") if it.get("revenueEstimate") is not None else 0)` 更直白一點，但非阻塞，現狀已經正確。
- `tests/test_select_rows_covers_every_date_and_ranks_by_revenue_desc_with_none_last` 只測了「每個 date 剛好 20 筆、共 3 個 date」平均分配的情境，沒有覆蓋「date 數量不均（例如某天筆數遠少於其他天）」時 round-robin 是否仍把省下的名額分給其他天」的邊界；目前程式邏輯（`while` loop 逐輪掃描、某天 round_idx 超出即跳過但 `added_any` 由其他天撐住）看起來是對的，但沒有測試直接鎖住這個行為，之後若重構容易沒發現回歸。非阻塞，可視情況於下個 sprint 補測。

### ✅ What looks good
- `_select_rows` 短路條件 `len(items) <= limit` 避免小量結果多做一次分組排序，效能上沒有多餘開銷
- 截斷邏輯放在白名單過濾（`EARNINGS_FIELDS`）**之前**執行，`upsert_earnings_calendar` 與工具回傳值使用同一份已截斷資料，`test_earnings_calendar_upserts_thinned_data` 驗證的「瘦身資料同時用於 upsert 與回傳」設計決定不受本次改動破壞
- 逐檔案確認呼叫端無隱含順序假設：`scheduler_handler.py` 一律帶 `symbol=` 逐檔查詢（回傳筆數遠低於 50，`_select_rows` 短路直接原樣回傳），本次改動不影響排程通知路徑
- httpx log 修正精準（只調整必要的 logger，未動 `logging.basicConfig` 的整體等級），不影響其他模組的 INFO log 可見度
- 新 playbook section 觸發語刻意避開既有行事曆 section 高頻詞（本週/公司/發布財報），BM25 分數獨立重測與交叉污染風險評估一致，未觀察到誤命中
- diff 完全 surgical：只動了 `chat_cli.py`（3 行）、`earnings.py`（新增 `_select_rows` + 呼叫點 2 行改動）、`earnings.md`（新增一個 section）與對應測試，無不相關改動

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞項目；🟢 建議項目可留待下次觸碰此檔案時順手處理
