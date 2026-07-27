# QA Report: rate-limit-and-fast-path

- **Contract**: 無 contract，對照需求驗收（production 429 事件：「META的財報是什麼時候」9 次 tool_use_failed 重試燒 token、撞 TPD、429 回 413 誤導文案、模型用 2024 過去年份）
- **Date**: 2026-07-27（Round 1）／2026-07-27（Round 2 複驗）
- **Verdict**: ✅ Pass（Round 2 複驗後，Round 1 的 🔴 Critical 已修正確認）

## 複驗紀錄（Round 2）

Generator 修正內容（只動 `src/agent/fast_path.py` 與 `tests/test_fast_path.py`）：
1. 新增 `_is_ticker_adjacent_to_earnings`：ticker 必須緊鄰「財報」關鍵字（只允許空白／「的」／單一日期詞+「發」作連接），取代原本「三條件全句各自獨立掃描、互不要求鄰近」的寬鬆比對
2. 新增 `_EXCLUDED_TICKERS` 排除清單（AI、IT、ETF、GDP、CPI、SEC、IPO、ESG、CNN、CEO、CFO、EPS、FED、PMI、CES），即使緊鄰「財報」也直接排除
3. `tests/test_fast_path.py` 負例集合補 4 條（AI 泛用問句、AI 嵌入複合詞、CNN、EPS 緊鄰財報），自報 398 passed

複驗方式：獨立讀碼確認邏輯順序（`extract_ticker` → 排除清單 → 鄰近性 regex，三關卡皆需通過才會呼叫 `get_earnings_calendar`），並用 mock `get_earnings_calendar` 重新實測 Round 1 揪出的所有誤攔 repro case，另外再構造多組邊角 case 壓測：

| 測試句 | Round 1 結果 | Round 2 結果 |
|---|---|---|
| `AI 相關企業的財報公布日期是什麼時候` | 🔴 誤攔（回 C3.ai 假 EPS/營收數字） | ✅ None |
| `美股AI概念股財報日期` | 🔴 誤攔 | ✅ None |
| `IT 產業財報公布日期` | 🔴 誤攔 | ✅ None |
| `CNN 報導了哪些財報日期` | 🔴 誤攔（回「查無資料」，答非所問） | ✅ None |
| `SEC 規定的財報揭露日期` | 🔴 誤攔 | ✅ None |
| `GDP 公布日期跟財報季重疊嗎` | 🔴 誤攔 | ✅ None |
| `META的財報是什麼時候` / `AAPL 財報日期` / `NVDA 什麼時候發財報` / `TSLA 財報何時公布`（既有正例） | ✅ HIT | ✅ HIT（未破壞既有正例） |
| `BRK.B 財報日期`（帶 `.` 後綴 ticker） | 未測 | ✅ HIT（正確識別帶後綴 ticker） |
| `查一下TSM財報什麼時候`（動詞前綴） | 未測 | ✅ HIT（前綴不影響鄰近性判斷） |
| `EPS 財報日期怎麼看`（財務指標縮寫緊鄰財報） | 未測 | ✅ None（排除清單擋下） |
| `請問GOOGL的財報是什麼時候公布` | 未測 | ✅ HIT |
| `NOW 這檔股票財報日期` / `ON 半導體財報日期`（真實 ticker，但與財報之間隔著其他詞） | 未測 | ✅ None（漏接，非誤攔——鄰近性要求嚴格，`這檔股票`／`半導體` 破壞鄰近，符合「寧可漏接不誤攔」） |
| `TSLA 和 NOW 的財報日期` / `NOW 我想問 NVDA 的財報日期`（多實體問句） | 未測 | ✅ None（`extract_ticker` 只抓第一個 token，鄰近性驗證失敗即直接回 None，不會誤答成句中任何一個公司；不會發生「抓到 A 卻鄰近性驗證用 A、實際使用者問的是 B」的錯配） |
| `V 財報日期` / `C 財報日期` / `T 財報日期`（單字母真實 ticker，理論邊角） | 未測 | HIT（見下方 🟢 Suggestion，評估為低風險不阻塞） |

全測試重跑（停用 sandbox）：**398 passed / 0 failed**，與 generator 自報一致，無 regression。

結論：Round 1 揪出的誤攔 case 全數修正為 None；既有正例與新增邊角正例（後綴 ticker、動詞前綴）皆正常運作；多實體問句、真實 ticker 但非鄰近措辭皆安全回退為 None（漏接不誤攔，符合設計原則）。Round 1 的 🔴 Critical 現已解除，未發現新的誤攔（false positive）風險。僅有極低風險的理論邊角（單字母真實 ticker，見 🟢 Suggestion）不影響合併判斷。

---

## Round 1（已解決，記錄留存供追溯）

- **Verdict（Round 1）**：🔴 Critical（fast_path 誤攔可回傳與問題無關的具體財報數字，違反「寧可漏接不誤攔」設計原則）

## 需求 checklist

| 需求 | Status | Note |
|---|---|---|
| 1. APIStatusError 依 status_code 分流（413/429/其他） | ✅ | `agent.py:192-196`，三分流文案清楚區隔，不再共用 413 舊文案 |
| 2. tool_use_failed 累計 >3 次直接回覆、不再重試 | ✅ | `agent.py:157,171-178`，counter 橫跨多輪 loop 累計（loop 外宣告），第 4 次呼叫即 return，測試 `call_count == MAX_TOOL_USE_FAILED_RETRIES + 1` 驗證正確 |
| 3. playbook 特定個股 section 補開放式日期查詢規則 | ✅ | `knowledge/playbooks/earnings.md` 特定個股 section 補「今天→+90 天、禁過去年份」規則，`test_prompts.py` 有對應斷言 |
| 4. `fast_path.py` `try_fast_answer` 高信心句型跳過 LLM | ✅（Round 2 修正後） | Round 1 發現 ticker 判斷規則過寬會誤攔非個股問句（見下方 🔴 Critical 與複驗紀錄）；Round 2 補鄰近性 regex + 排除清單後複驗通過，功能與模板正確 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- Sandbox 內結果：381 passed / 13 errors（`test_local_integration.py` 本機起 `ThreadingHTTPServer` 綁 socket，sandbox 拒絕 bind，屬環境限制非回歸，與先前多份 QA report 一致）
- 停用 sandbox 重跑：**394 passed / 0 failed**，與 generator 自報一致
- 既有 suite regression：✅ 無

## Findings（Round 1；🔴 已於 Round 2 修正確認，見上方複驗紀錄）

### 🔴 Critical（Round 1 發現，Round 2 已修正 — Must Fix → Fixed）

- **`src/agent/fast_path.py:22,28,36-43`（ticker 抽取規則過寬，未要求 ticker 緊鄰「財報」）**：`extract_ticker` 對任何 1-10 個大寫（或可轉大寫）英文字母的獨立 token 都視為候選 ticker，且 `try_fast_answer` 只要求「財報」關鍵字、日期詞、ticker 三者「同時出現在整句任何位置」，不要求彼此鄰近。這讓常見的財經英文縮寫／loanword（尤其 `AI`——C3.ai 的真實代碼，在 2026 年 AI 熱潮下極可能被使用者當一般詞彙打出）會被誤判成明確股票代碼查詢，繞過 LLM 直接回覆看似精確、實則答非所問的財報數字。

  實測（mock `get_earnings_calendar` 回傳假資料，模擬真實會有資料的情況）：
  ```
  >>> try_fast_answer("AI 相關企業的財報公布日期是什麼時候")
  '*AI* 未來 90 天內的財報日期：\n• 2026-08-15（盤後），EPS 預估 $-0.10，營收預估 100.0M'
  >>> try_fast_answer("美股AI概念股財報日期")
  '*AI* 未來 90 天內的財報日期：\n• 2026-08-15（盤後），EPS 預估 $-0.10，營收預估 100.0M'
  >>> try_fast_answer("IT 產業財報公布日期")
  '*IT* 未來 90 天內的財報日期：\n• 2026-08-15（盤後）...'
  ```
  使用者問的是「AI 概念股／AI 相關企業」的財報公布日期（一個泛用類別問題），得到的卻是 C3.ai（ticker `AI`）單一公司的具體 EPS／營收預估，且用戶不會知道系統把「AI」當成了股票代碼——這比原本 production 事件的「文案誤導」更嚴重，因為它是**帶著具體數字的自信誤答**，且完全跳過 LLM，沒有任何機會被上下文修正。

  較輕微但同類的例子（回傳「查無資料」而非幻覺數字，但仍答非所問）：
  ```
  >>> try_fast_answer("CNN 報導了哪些財報日期")
  '未來 90 天內查無 CNN 的財報日期'
  >>> try_fast_answer("SEC 規定的財報揭露日期")
  '未來 90 天內查無 SEC 的財報日期'
  >>> try_fast_answer("GDP 公布日期跟財報季重疊嗎")
  '未來 90 天內查無 GDP 的財報日期'
  ```
  這幾個是財經對話中常見的機構／總經縮寫（CNN、SEC、GDP、CPI、ETF、IPO 皆符合 `[A-Z]{1,10}` 樣式），只要句中同時出現「財報」與任一日期詞就會誤觸發。

  對照 generator 自己在 `try_fast_answer` docstring 中明訂的原則「Deliberately conservative: any ambiguity falls through to `None`」，以及本次驗收要求的「寧可漏接不誤攔」，目前的比對邏輯只檢查「三個條件是否同時出現在全句任意位置」，並未檢查 ticker 與「財報」關鍵字的**鄰近性**，因此無法排除上述泛用詞／機構縮寫的誤攔。`tests/test_fast_path.py` 的負例集合（`"本週有什麼公司發布財報"`、`"META 最近新聞"`、`"META"`、`"META 的分析師預期"`）都不含這類「英文縮寫 + 財報 + 日期詞」的組合，完全沒有覆蓋到這個風險類別，儘管這正是本次驗收特別要求測的重點。

  建議修法：要求 ticker token 與「財報」關鍵字在句中緊鄰（例如 `TICKER` 後接 0-1 個字元的「的」或空白後直接接「財報」，或反過來「財報」後緊接 ticker），比對原始例句 `"META的財報是什麼時候"` / `"AAPL 財報日期"` 皆符合此緊鄰模式，可用更嚴格的單一 regex（如 `TICKER(的)?財報` 或 `財報(TICKER)?`）取代目前「分別在全句掃描三個獨立條件」的作法；或至少針對常見高風險縮寫（AI、IT、ETF、GDP、CPI、SEC、IPO、ESG、CNN 等）建立排除清單。兩者皆可行，但緊鄰性判斷更能從根本解決問題、不需要持續維護清單。

### 🟡 Warnings（Should Fix）

- 無（除上述 Critical 外，未發現需在合併前處理的中等問題）

### 🟢 Suggestions

- **`src/agent/agent.py:122-128`**：`try_fast_answer(user_message)` 與 `extract_ticker(user_message)` 各自對同一段文字重新跑一次 regex（後者純粹為了 log），對每一則訊息都多算一次、且在未命中財報關鍵字時這個 log 欄位其實無意義（永遠嘗試抽 ticker，即使句子跟財報完全無關）。可考慮讓 `try_fast_answer` 回傳 `(answer, ticker)` 或另開一個輕量函式，避免重複計算；非阻塞，純效能／可讀性小優化。
- **`src/agent/fast_path.py:19-21`**：註解已誠實標註「複製而非 import `SYMBOL_RE` 有同步風險」，目前兩處定義字元不差（已核對），但未來 `processor_handler.SYMBOL_RE` 若改動不會有任何自動化訊號提醒同步 `fast_path._SYMBOL_RE`。可考慮加一條輕量測試斷言兩個 regex pattern 字串相等，把「同步風險」從註解提升為可驗證的守門，防止未來 drift 靜默發生。
- **`src/agent/fast_path.py:41-46`（Round 2 新發現，低風險理論邊角）**：`_EXCLUDED_TICKERS` 目前只擋常見機構／指標縮寫，未涵蓋單字母真實 ticker（`V`=Visa、`C`=Citigroup、`T`=AT&T、`K`=Kellanova 等）。實測 `"V 財報日期"`／`"C 財報日期"` 等仍會 HIT。真實對話中「單一大寫字母＋財報＋日期詞」且緊鄰的自然語句極少見（多半會有其他文字介入而破壞鄰近性判斷），評估為低風險，不建議為此擴大排除清單增加維護負擔；若未來 log 觀察到實際誤攔再處理即可，非阻塞。

### ✅ What looks good

- APIStatusError 三分流（413/429/其他）文案清楚、`except BadRequestError` 置於 `except APIStatusError` 之前的既有子類別攔截順序未受影響，實測 `RateLimitError`／`APIStatusError(500)` 均正確分流，測試新增 `_server_error()` 覆蓋非 413/429 分支。
- tool_use_failed 重試上限邏輯正確：counter 宣告於 for-loop 外、跨輪次累計（非每輪重置），第 4 次即中止並回傳友善訊息，不再往下打 Groq，直接對應 production 事件根因（9 次重試燒光 TPD）；前 3 次重試行為（含把驗證錯誤訊息塞回 messages 讓模型修正）未被破壞。
- playbook 修改精準落在「特定個股財報與分析師預期查詢」section，只新增一句規則，未動其他既有指引；`test_prompts.py` 新增斷言與內容逐字對應。
- fast_path 與 playbook 注入順序正確：`run_agent` 開頭先跑 `try_fast_answer`，命中即直接 return，完全不進入 messages 組裝／playbook retrieval／Groq client；實測 log 序列確認 `fast_path` log 一定先於 `playbook_injection` log，未命中時才進入既有流程，不影響既有注入行為。
- 無循環 import：`fast_path.py` 只 import `agent.tools.earnings`，不 import `agent.agent` 或 `processor_handler`；`agent.py` import `fast_path` 屬單向依賴，`processor_handler.py -> agent.agent -> fast_path` 為線性鏈，無環。
- 模板輸出格式實測正確：有資料時列出日期＋時段（bmo/amc/dmh 對應盤前/盤後/盤中，缺失標「時段未定」）＋ EPS（`$` 格式）／營收（B/M 單位換算）；無資料時回覆「未來 90 天內查無 {ticker} 的財報日期」，符合 playbook 既有回覆要求的格式慣例。
- diff 範圍 surgical：只動 `src/agent/agent.py`、新增 `src/agent/fast_path.py`、`knowledge/playbooks/earnings.md` 一句話、對應測試檔（`tests/test_agent.py`、`tests/test_prompts.py`、新增 `tests/test_fast_path.py`），無不相關改動。Round 2 修正同樣 surgical，只動 `fast_path.py` 與 `test_fast_path.py`。
- 測試全綠：Round 1 為 394 passed、Round 2 為 398 passed（皆與 generator 自報一致），停用 sandbox 後複驗。

## 結論（Round 2 最終）

- 是否可合併：**是**
- Round 1 的 🔴 Critical（fast_path 誤攔 AI／IT／CNN／SEC／GDP 等英文縮寫）已在 Round 2 透過「鄰近性 regex + 排除清單」雙重機制修正，複驗確認所有原始 repro case 皆回 None，既有正例與新增邊角正例不受影響，多實體問句與真實 ticker 但非鄰近措辭皆安全回退，全測試綠燈（398 passed）
- 剩餘事項（不阻塞合併，可日後視情況處理）：
  1.（🟢）`agent.py` 內 `try_fast_answer`／`extract_ticker` 重複計算，可考慮合併為單次呼叫
  2.（🟢）`fast_path._SYMBOL_RE` 與 `processor_handler.SYMBOL_RE` 手動同步缺自動化守門測試
  3.（🟢）單字母真實 ticker（V/C/T/K）的理論邊角誤攔風險極低，暫不需擴大排除清單
