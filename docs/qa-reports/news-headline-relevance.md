# QA Report: news-headline-relevance

- **Contract**: 無 contract（feature skill，直接對照需求描述驗收）
- **Date**: 2026-07-28
- **Verdict**: ✅ Pass

## 需求 checklist

| 需求 | Status | Note |
|---|---|---|
| 1. Big 7 symbol → 公司名稱別名對照，headline 含 ticker 或別名（不分大小寫、word boundary）才收 | ✅ | `src/agent/tools/news.py:52-82`：`BIG7_ALIASES` 涵蓋 7 檔（AAPL/MSFT/GOOGL/AMZN/NVDA/META/TSLA），與 `market_summary.BIG7` 清單一致；`_BIG7_HEADLINE_PATTERNS` 用 `re.escape` + `\b...\b` + `re.IGNORECASE` 預編譯；未知 symbol 退回只比對 ticker（`is_headline_relevant` line 79-81） |
| 2. 過濾套在 `refresh_news()` 的 big7 迴圈，junk 過濾之後、入庫之前；AI 新聞不動 | ✅ | `market_summary.py:138-152`：順序為 `not headline or is_junk_headline` → `is_headline_relevant` → append，符合要求；AI 區塊（154-177）未改動（diff 確認） |
| 3. 只看 headline，不看 summary | ✅ | `is_headline_relevant(symbol, headline)` 簽章只吃 headline，呼叫端也只傳 headline（`market_summary.py:142`） |
| 4. 被濾掉的記 log，方便觀察誤殺率 | ✅ | `market_summary.py:143` `log.info("headline filtered as not about %s: %s", symbol, headline)`，`%s` lazy-format 風格與同檔其他 log 呼叫一致（118/136/157/180/227 行） |
| 5. `get_stock_news` 使用者查詢工具行為不變 | ✅ | `news.py:107-133` 的 `get_stock_news()` 未呼叫 `is_headline_relevant`，只沿用既有 `is_junk_headline`；既有測試 `test_news_all_real_returns_unfiltered` 持續通過，驗證輸出未受影響 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`（worktree 內執行）
- 沙箱內結果：433 passed / 13 errors（`test_local_integration.py` 因 sandbox 禁止 `socket.bind` 觸發 `PermissionError`，與本次改動無關，屬既有的本地 TCP stub server 測試）
- 停用沙箱重跑：**446 passed, 0 failed**，與 generator 自報一致
- 既有 suite regression：✅ 無（沙箱錯誤已排除為環境因素，非程式問題）

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- **`src/agent/tools/news.py:58`**：`META` 別名含 `"Meta"`，是常見英文詞根，`\b(?:META|Meta|Facebook)\b` 理論上會誤判如 "Meta-analysis of clinical trial data" 這類無關財經新聞（連字號兩側都是 word boundary）。目前設計已用 log 記錄被濾掉／通過的 headline，之後可用 log 觀察誤殺率／誤放率是否需要加更嚴格的規則（例如搭配 finance 語境詞）；本次不阻塞，屬需求文件本身就承認的已知 trade-off（heuristic，非 100% 精準）。
- **`src/agent/tools/news.py:52-60`**：別名對照目前是靜態 dict，若之後 Big 7 名單變動（如新增/移除公司）需要同步維護 `market_summary.BIG7` 與 `BIG7_ALIASES` 兩處；目前兩者一致，暫不需重構（單一改動點少，維持現狀符合「不做投機性抽象」原則）。

### ✅ What looks good
- 正則預編譯在 module load 時一次完成（`_BIG7_HEADLINE_PATTERNS`），避免迴圈內重複 compile，效能無虞。
- `re.escape` 套用在 symbol 與別名上，避免特殊字元（理論上目前資料無此風險，但寫法穩健）。
- 未知 symbol fallback（`get_stock_news` 之外的路徑理論上不會走到，但函式簽章對外部呼叫保持防禦性）测試覆蓋（`test_is_headline_relevant_unknown_symbol_falls_back_to_ticker_match`）。
- log 呼叫風格（lazy `%s` formatting，不用 f-string）與同檔既有慣例一致。
- 測試覆蓋對應需求逐條：ticker/別名相符（10 組 parametrize）、跨公司誤判（Intel/Apple regression case）、word boundary（Metaverse）、大小寫、None/空字串、未知 symbol fallback，共 16 個新 case；`test_market_summary.py` 新增的 `test_refresh_news_filters_headlines_not_about_the_symbol` 端到端驗證 log 訊息與入庫結果。
- 改動範圍精準：只新增函式與資料結構、market_summary.py 的迴圈插入兩行過濾邏輯，未觸碰不相關程式碼，符合「surgical changes」原則。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞項；建議上線後觀察 `headline filtered as not about %s` log 的誤殺/誤放比例，視情況決定是否需要為 "Meta" 等常見詞加語境限定（非本次必要）。
