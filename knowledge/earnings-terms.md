# 財報術語解讀

## EPS beat / miss 是什麼意思

EPS（每股盈餘，Earnings Per Share）beat（超出預期）表示實際 EPS 高於分析師共識預期（consensus estimate）；miss（不及預期）表示實際 EPS 低於共識預期。EPS 剛好等於預期則稱為 in-line（符合預期）。判斷 beat/miss 要拿實際公布值與 `get_analyst_estimates` 回傳的共識 EPS 比較。

## guidance 財測 / 展望是什麼

guidance（財測、展望、指引）是公司管理層對未來一段時間（下一季或全年）營收、EPS 等財務數字的預估，通常在財報發布時一併公布。guidance 若高於市場預期稱為 raise（上修）或 guidance beat；低於市場預期稱為 cut（下修）或 guidance miss。市場對財報的反應常常更看重 guidance 而非當季實際數字。

## 營收 beat / miss revenue 是什麼意思

營收（revenue，收入）beat 表示實際營收高於分析師共識預期；營收 miss 表示實際營收低於共識預期。與 EPS beat/miss 是兩個獨立指標，一家公司可能 EPS beat 但營收 miss，或反之。

## bmo amc dmh 財報公布時段代碼是什麼

Finnhub earnings calendar 的 `hour` 欄位用以下代碼表示財報公布時段：`bmo`（before market open，盤前，開盤前公布）、`amc`（after market close，盤後，收盤後公布）、`dmh`（during market hours，盤中，交易時段中公布）。查 `get_earnings_calendar` 結果時可用這三個代碼判斷公布時間點。

## consensus 分析師共識是什麼

consensus（共識、分析師共識預期）是多位分析師對某公司財務指標（EPS、營收等）預估值的平均或中位數，代表市場普遍預期。`get_analyst_estimates` 回傳的 EPS 預估、評級分佈都屬於分析師共識資料，是判斷財報 beat/miss 的比較基準。
