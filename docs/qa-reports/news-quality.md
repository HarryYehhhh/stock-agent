# QA Report: news-quality

- **對照**：需求（無 contract，見下方「需求」區塊）+ 程式碼品質
- **Date**: 2026-07-25
- **Verdict**: ✅ Pass（第 2 輪複驗，round 1 的 🔴🟡 皆已回修並驗證）

## 需求 checklist

| 項目 | Status | Note |
|---|---|---|
| 1. `get_stock_news` 過濾行銷導流 / listicle headline（regex, case-insensitive） | ✅ | 過濾發生在切 top-5 之前；round 1 找到的 4 個過殺反例（subscriber/top gainer/stock to watch/most actively traded）皆已在 round 2 修正並實測不再誤殺，垃圾判定能力未退化 |
| 2. `SYSTEM_INSTRUCTION` 補「歸納整合、翻繁中、忽略無實質內容、不逐條翻譯」 | ✅ | 已補，且與既有「主動翻譯成繁體中文」句不矛盾（一句講翻譯目標語言，一句講呈現方式是歸納而非逐條翻譯） |
| 3. 附過濾單元測試（混合/全垃圾/全正常/大小寫邊界） | ✅ | 涵蓋 mixed/all-junk/all-real/case-insensitive/None-empty，round 2 額外把 round 1 的 5 個邊界反例（subscriber×2、top gainer、stock to watch、most actively traded）併入 `test_is_junk_headline_allows_real_news`，補上原本缺的回歸覆蓋 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：244 passed, 12 errors（`tests/test_local_integration.py`，`PermissionError: [Errno 1] Operation not permitted` 於 `socket.bind`；與本次改動無關的既有沙盒限制，過去多份 QA report 已記錄相同現象）
- 非沙盒重跑：**256 passed, 0 failed**（與 generator round 2 自報一致）
- 既有 `test_market_summary.py` fixture headline 皆未命中 `JUNK_HEADLINE_PATTERN`，`refresh_news` 套用過濾後行為未變、無隱性回歸

## Round 2 複驗：round 1 的反例逐一重測

對修正後的 `JUNK_HEADLINE_PATTERN` 用實際 regex 重跑以下所有 round 1 反例，結果全部由「誤判為垃圾」轉為「正確判定為真新聞」：

| Headline | Round 1 | Round 2 |
|---|---|---|
| "Netflix subscriber growth beats estimates in Q2" | 🔴 誤殺（`subscribe` 子字串匹配 subscriber） | ✅ 不再誤殺（`\bsubscribe\b` 需完整字） |
| "Disney+ subscribers hit record high" | 🔴 誤殺 | ✅ 不再誤殺 |
| "Tesla was the top gainer in the S&P 500 today after earnings beat" | 🔴 誤殺（獨立 `top gainer` 單數匹配） | ✅ 不再誤殺（獨立 `top gainers?/losers?` 規則已整條移除，只留 `gainers? (and\|&) losers?` 成對形式） |
| "Nvidia shares jump 8%, becomes top gainer on Nasdaq amid AI chip demand" | 🔴 誤殺 | ✅ 不再誤殺 |
| "Apple is a stock to watch after strong iPhone sales data" | 🟡 疑慮（單數 `stock to watch` 也命中） | ✅ 不再誤殺（`stocks\s+to\s+watch` 已強制複數） |
| "AMC among most actively traded stocks as retail interest surges" | 🟡 疑慮（`most active...stocks` 跨標點容錯過寬） | ✅ 不再誤殺（該規則整條移除） |

同時確認垃圾偵測能力未因收斂而退化，原型混合案例（listicle + 導流片語組合）仍正確命中：

- `"Most active S&P500 stocks in today's session — join us as we dive into today's biggest movers"` → 命中 `join us`，仍正確判定為垃圾（「most active stocks」本身雖已不再單獨作為判斷依據，但真實 listicle 通常會搭配 CTA 片語，例如本例的 "join us"，仍能被其他規則攔下）
- 其餘既有導流片語（join us / tune in / sign up / register now / webinar / stocks to watch（複數）/ trending tickers / gainers and losers（成對））皆維持原本的正確判定

## Findings

### 🔴 Critical

無。Round 1 找到的 2 個 Critical（`subscribe` 子字串誤殺、獨立 `top gainer/loser` 單數誤殺）皆已在 round 2 修正並經上表逐一實測確認。

### 🟡 Warnings

無。Round 1 找到的 2 個 Warning（`stock to watch` 單數誤殺、`most active...stocks` 跨標點誤殺）皆已在 round 2 修正（前者強制複數、後者整條規則移除）並經實測確認不再誤殺。

### 🟢 Suggestions

- `most active...stocks` 規則整條移除後，若之後 Finnhub 出現「純粹只有 `Most active stocks today` 這種標題、完全沒有搭配 join us/webinar 等 CTA 片語」的 listicle，會因此漏放（漏殺）。這正是需求要求的取捨方向（寧可漏殺不過殺），故非缺陷，僅記錄供未來觀察 log 決定是否需要更精準的規則。
- `src/agent/tools/news.py` 的檔案頭部註解清楚記錄了「為何刻意不做某些規則」的理由（含具體反例），對後續維護者理解「為何看似能收斂卻沒收斂」很有幫助，值得肯定。

### ✅ What looks good

- `is_junk_headline()` 抽成獨立函式並在 `get_stock_news` 與 `market_summary.refresh_news`（Big7 + AI 新聞兩處迴圈）共用，避免邏輯重複，且過濾發生在切 top-5 之前，符合「先過濾再截斷」的正確順序。
- `market_summary.py` 對 `tools.news` 的 import 方向正確（`market_summary` → `tools.news`，非反向），未製造新的循環引入；`tools/__init__.py` 的 import 鏈（`market_summary_tool` → `agent.market_summary` → `agent.tools.news`）本身也不回頭 import `tools/__init__`，無循環風險。
- `SYSTEM_INSTRUCTION` 新增句與既有「主動翻譯成繁體中文」句語意互補、不矛盾：一句定翻譯目標語言，一句定呈現方式（歸納整合而非逐條翻譯）。
- `is_junk_headline(None)` / `is_junk_headline("")` 正確回傳 `False`，不會誤判空值為垃圾。
- 既有 `test_market_summary.py` 測試在套用新過濾後全數維持綠燈，沒有因新規則悄悄改變行為卻未被發現的情況。
- Round 2 把所有 round 1 找到的邊界反例（5 個）直接併入正式回歸測試（`test_is_junk_headline_allows_real_news`），而非只在對話中口頭確認，往後同類回歸能被測試自動攔下。

## 結論

- 是否可合併：**是**
- 循環次數：2（round 1 發現 2🔴 2🟡，round 2 全數修正並實測驗證，round 2 無新增發現）
- 未解 P0/P1：0
