# 分析框架

## 怎麼判斷一檔股票財報好不好

判斷財報好壞可組合三個現有工具：先用 `get_analyst_estimates` 取得分析師共識 EPS 與評級分佈作為比較基準；用 `get_earnings_calendar` 確認財報發布日期與時段（bmo/amc/dmh，見財報術語 section）；財報公布後用 `get_stock_news` 看市場對財報的即時反應（新聞標題、分析師是否上修/下修目標價）。三者合併判斷比只看實際 EPS 數字更完整。

## 財報公布前該查什麼 pre-earnings checklist

財報公布前建議依序查：`get_earnings_calendar` 確認發布日期是否已公布（尚未公布不代表沒有財報，只是日期還沒排定）；`get_analyst_estimates` 取得分析師共識 EPS 與評級，作為公布後判斷 beat/miss 的基準；`get_stock_news` 看近期新聞是否有分析師預期上修/下修的訊號。

## 怎麼看大盤整體市況 market overview

想了解整體美股市場（大盤）狀況時，直接呼叫 `get_market_summary`，不要用 `get_quote` 猜指數代號（DJIA、SPX 等查不到，見指數代號對照 section）。`get_market_summary` 已整合主要指數表現與科技股/AI 產業新聞，適合用來回答大盤現況、市場概況相關提問。

## 財報公布後該怎麼追蹤 post-earnings follow-up

財報公布後，用實際公布的 EPS、營收與 `get_analyst_estimates` 取得的共識比較，判斷是否 beat/miss（見財報術語 section）；再用 `get_stock_news` 查財報公布後市場反應與分析師評論，確認股價變動是否符合財報結果的預期方向。
