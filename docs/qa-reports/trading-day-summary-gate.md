# QA Report: trading-day-summary-gate

- **Contract**: 無 contract，對照使用者需求（見本輪 dispatch 訊息）+ 程式碼品質
- **Date**: 2026-07-26
- **Verdict**: ⚠️ Pass with issues

## 需求 checklist
| 需求 | Status | Note |
|---|---|---|
| 1. 交易日判斷（zoneinfo NY 日期、週末不打 API、假日清單 + tradingHour 空字串判斷、半日仍算交易日） | ✅ | `_is_trading_day()`（`src/agent/market_summary.py:94-113`）邏輯正確：`ny_date.weekday() >= 5` 先擋週末（不呼叫 API，測試 `fetch.assert_not_called()` 驗證），假日比對用 `h.get("atDate") == iso and not h.get("tradingHour")`，空字串與缺欄位皆視為全日休市，半日（`tradingHour` 非空）維持交易日 |
| 2. 只 gate `push_daily_summary`，refresh/generate 之前 return，log 一筆 skip 帶日期上下文 | ✅（附註見 🟡） | `push_daily_summary()` 開頭第一行即呼叫 `_is_trading_day()`（`market_summary.py:339-341`），早於 `refresh_news`/`generate_market_summary`；log 訊息存在但日期欄位有 timezone 落差，見下方 🟡 |
| 3. `scheduler_handler.py`（`_run`/`_notify_local`/sync 階段）不動 | ✅ | `git diff` 確認 `scheduler_handler.py` 完全未改動 |
| 4. Finnhub 假日 API 失敗 fail-open + log warning | ✅ | `_is_trading_day()` 的 `try/except Exception` 捕獲後 `log.warning(...)` 並 `return True`；測試 `test_is_trading_day_fails_open_when_holiday_check_errors` 驗證 |
| 5. 測試覆蓋（週末/全日休市/半日/平日/API 失敗 fail-open，全 mock） | ✅ | 新增 6 條 `_is_trading_day`/`_fetch_market_holidays` 單元測試 + 2 條 `push_daily_summary` gate 整合測試，皆用 monkeypatch，無真實 API 呼叫 |

## Test results
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙箱內結果：318 passed / 0 failed / 12 errors（`test_local_integration.py` 因沙箱擋 `socket.bind`，`PermissionError: Operation not permitted`，與本次改動無關）
- 停用沙箱重跑：**330 passed / 0 failed**（含 12 條 local integration），與 generator 自報一致
- `tests/test_market_summary.py` 單獨跑：32 passed（含新增 8 條）
- 既有 suite regression：✅ 無。今日（2026-07-26）為週日，若無 `monkeypatch.setattr(ms, "_is_trading_day", lambda: True)`，既有 `push_daily_summary` 相關測試會被新 gate 擋下而失敗——已實測驗證這個 monkeypatch 是必要的（非過度防禦），且僅補在直接呼叫 `push_daily_summary()` 的測試上，未動到 `refresh_news`/`generate_market_summary` 等其他函式的既有測試，改法合理、未讓既有覆蓋失效

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）
- **`src/agent/market_summary.py:340`**：skip log 用 `date.today().isoformat()`，但實際判斷交易日的基準是 `_is_trading_day()` 內部算出的 `ny_date`（America/New_York）。Lambda 執行環境（容器內系統時區為 UTC）下，這兩個日期在 notify 排程執行當下（10:00 Asia/Taipei）會系統性地差一天：實測 `2026-07-27 10:00 Asia/Taipei` 時，`date.today()`（UTC）= `2026-07-27`，但 `_is_trading_day()` 實際檢查的 NY 日期 = `2026-07-26`。也就是 skip log 印出的日期永遠比實際被判定為非交易日的那一天晚一天。這屬於純 log/observability 問題，不影響 gate 本身的推播正確性（使用者依然正確地在非交易日收不到推播），但會誤導依賴 CloudWatch log 日期欄位做每日健檢/事後排查的流程（例如日常 10:30 Taipei 的排程健檢例行工作，會查不到 log 裡預期的日期，或誤判成隔天才 skip）。
  建議修法：讓 `_is_trading_day()` 回傳它算出的 `ny_date`（或另外開一個小 helper 回傳该日期），`push_daily_summary()` 的 skip log 改用這個 NY 日期而非 `date.today()`，讓 log 與實際判斷基準一致。

### 🟢 Suggestions
- 無新增可提但不必擋的問題；`_fetch_market_holidays()` 命名、參數風格與既有 `_fetch_company_news`/`_fetch_tech_news` 一致，`_is_trading_day()` docstring 清楚交代了 10:00 Taipei = 22:00 NY 前一日收盤的時間換算依據，可讀性佳。

### ✅ What looks good
- 週末判斷提前 return，不浪費一次 Finnhub API 呼叫（有測試明確斷言 `fetch.assert_not_called()`）
- fail-open 設計正確且有測試覆蓋，符合「假日 API 掛掉不能讓推播整個消失」的需求
- gate 放置位置正確：在 `refresh_news`/`generate_market_summary` 之前就 return，不會浪費新聞抓取或 Groq 呼叫額度
- `scheduler_handler.py`／`refresh_news()`／sync 階段完全未觸碰，diff 範圍精準對應需求（surgical）
- 測試用凍結 `datetime.now(tz)` 的手法（`_freeze_ny_now`）刻意不模擬真實 zoneinfo 換算，只驗證業務邏輯本身（週末/假日/半日判斷），职责边界清楚、避免測試依賴系統時區
- 既有 `push_daily_summary` 測試補 `_is_trading_day → True` monkeypatch 是必要且精準的修補，不影響其他函式測試

## 結論
- 是否可合併：是（🟡 為 log 日期欄位的 observability 問題，不影響功能正確性，可視情況先合併、之後再修，或這輪一併回修）
- 最該先處理的 1 件事：`push_daily_summary()` 的 skip log 改用 `_is_trading_day()` 實際判斷的 NY 日期，而非 `date.today()`，避免日常 CloudWatch 健檢查到錯誤日期

## 後續處理（2026-07-26）
🟡 skip log 日期問題已於同輪回修：拆出 `_ny_today()` helper，`_is_trading_day(ny_date)` 改由呼叫端傳入日期，skip log 改帶實際判斷用的 NY 日期（`ny_date=...`）。新增 regression test `test_push_daily_summary_logs_skip_with_ny_date_not_system_today` 驗證 log 帶 NY 日期而非系統日期。全套測試 331 passed / 0 failed。
