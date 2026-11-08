# QA Report: earnings-summary-news-grounding

- **需求來源**：使用者需求（修正財報通知生成時出現過季產品如 iPhone 15 與虛構 AI 加速器晶片的幻覺問題，採用第二種解法：由後端在生成前調取該 Symbol 最新新聞注入 Prompt）
- **Date**: 2026-11-08
- **Verdict**: ✅ Pass

## 需求 checklist

| 項目 | 狀態 | 備註 |
|---|---|---|
| `_company_summary` 調用前調取 Symbol 最近新聞 | ✅ | `scheduler_handler.py` 新增 `_fetch_company_news_context(symbol, max_items=3)`，呼叫 `get_stock_news(symbol, days_back=7)` 取得至多 3 則 headline 與 summary |
| 調取失敗或無新聞時優雅降級 | ✅ | `_fetch_company_news_context` 具備完整的 try/except 保護，異常或無新聞時回傳 `（近期無相關新聞）`，不拋出錯誤中斷發送 |
| `COMPANY_PROMPT_TEMPLATE` 擴充新聞注入與反幻覺限制 | ✅ | `prompts.py` 在模板中加入 `近期新聞參考：\n{news}`，並新增【重要限制】：嚴禁憑空捏造產品型號、營運狀況；重點必須依據上方新聞撰寫，無新聞時僅依據快取數據說明財報時間與預期 |
| 摘要生成注入新聞上下文 | ✅ | `_company_summary` 於 `COMPANY_PROMPT_TEMPLATE.format` 中傳入 `news=news_context` |
| 邊界測試與防回歸測試補齊 | ✅ | `tests/test_scheduler.py` 新增 `test_company_summary_injects_fetched_news_into_prompt` 與 `test_company_summary_handles_empty_or_failed_news_gracefully`；`tests/test_prompts.py` 新增 `test_company_prompt_template_has_news_and_anti_hallucination_guards` |
| 既有測試外部調用邊界保護 | ✅ | `tests/test_scheduler.py` 與 `tests/test_watchlist_notify_flow.py` 的 mock 補上 `get_stock_news` 與 `/company-news` 路徑處理，不向真實外部 Finnhub 發出連線 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 結果：**586 passed in 23.12s**（0 failed，0 skipped）
- Local integration 測試：`PYTHONPATH=src uv run pytest tests/test_local_integration.py -v`
- 結果：**27 passed in 7.64s**
- SAM Template 檢查：`cd infra && sam validate --region ap-northeast-1 --lint`
- 結果：Template is valid

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
無。

### ✅ What looks good
- 實作嚴格遵循「最小精準變更」原則，僅修改 `src/agent/prompts.py` 與 `src/agent/scheduler_handler.py`，不改動 DB Schema，也不變動其他模組。
- `_fetch_company_news_context` 截斷過長 summary 至 150 字元，兼顧 token 預算與資訊完整性。
- 新增與更新的測試完整涵蓋「新聞注入」、「無新聞降級」與「新聞查詢異常降級」三大情境。

## 結論
- 是否可合併：是
