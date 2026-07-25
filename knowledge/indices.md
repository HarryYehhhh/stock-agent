# 指數代號對照

## 道瓊 / DJIA 指數怎麼查價

道瓊工業指數（Dow Jones Industrial Average，DJIA，道瓊）不是可直接查價的股票代號，`get_quote` 無法直接餵 `DJIA`。要查價請改用追蹤道瓊的 ETF 代號 `DIA`。

## S&P 500 / 標普 500 指數怎麼查價

S&P 500（標普 500、標準普爾 500）同樣不是可直接查價的代號。要查價請改用追蹤 S&P 500 的 ETF 代號 `SPY`。

## Nasdaq / 那斯達克指數怎麼查價

Nasdaq 100（那斯達克、納斯達克）指數不是可直接查價的代號。要查價請改用追蹤 Nasdaq 100 的 ETF 代號 `QQQ`。

## Russell 2000 / 羅素 2000 指數怎麼查價

Russell 2000（羅素 2000，小型股指數）不是可直接查價的代號。要查價請改用追蹤 Russell 2000 的 ETF 代號 `IWM`。

## 指數為什麼不能直接用 get_quote 查價

美股主要指數（DJIA、S&P 500、Nasdaq、Russell 2000 等）是計算出來的綜合指標，不是掛牌交易的個股或 ETF，Finnhub `get_quote` 只接受個股/ETF ticker，直接餵指數名稱（例如 `DJIA`、`SPX`）通常查無資料或回傳錯誤。正確做法：先查對照表取得追蹤該指數的 ETF 代號，再用該代號呼叫 `get_quote`。
