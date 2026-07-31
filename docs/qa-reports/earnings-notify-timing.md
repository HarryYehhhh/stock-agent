# QA Report: earnings-notify-timing

- **需求來源**：使用者需求（無 contract，見主 Claude 派工訊息）——修正財報通知時差問題
- **Date**: 2026-07-31
- **Verdict**: ✅ Pass

## 需求 checklist

| 項目 | 狀態 | 備註 |
|---|---|---|
| `_run()` 改查 `date.today()`（UTC）而非 `+1 day` | ✅ | `scheduler_handler.py:_run()`：`target_date = date.today().isoformat()` |
| `_notify_local` / `_company_summary` 參數改名 `tomorrow` → `target_date` | ✅ | 兩函式簽名與內部呼叫（`get_watchlist_earnings_for_date` / `unnotified_symbols` / `get_company_summary` / `upsert_company_summary` / `mark_notified`）全數同步改名，無漏改 |
| 推播文案「明日」措辭修正 | ✅ | 改為「📊 *即將發布財報提醒（{target_date}）*」，不再宣稱特定相對日期，符合「發布前 24 小時內」語意 |
| `COMPANY_PROMPT_TEMPLATE` 「明天」措辭修正 | ✅ | 改為「{symbol}將於 24 小時內（{target_date}）公布財報」，format kwarg 同步由 `tomorrow` 改 `target_date` |
| dedup / company_summaries 以日期為 key 自然分流，不動 schema | ✅ | `db/backend.py` 內 `get_watchlist_earnings_for_date` / `unnotified_symbols` / `mark_notified` / `get_company_summary` / `upsert_company_summary` 簽名皆為既有通用參數名 `earning_date`，本次改動未觸碰 `db/backend.py` |
| 不動 `sync_earnings()` | ✅ | `git diff` 確認 `sync_earnings()` 函式本體零改動 |
| 需有測試明確驗證新目標日期行為（notify 查今天 UTC 而非明天） | ✅ | 新增 `tests/test_scheduler.py::test_run_notifies_for_todays_date_not_tomorrow`：mock `_notify_local`，斷言 `_run()` 以 `date.today().isoformat()`（非 `+1`）呼叫，並附回歸說明 docstring |
| 既有測試日期常數同步更新，不弱化斷言 | ✅ | `TOMORROW` → `TARGET_DATE`/`today`（`test_scheduler.py`、`test_watchlist_notify_flow.py`、`test_local_integration.py`），逐一核對斷言邏輯本身未被放寬，僅日期語意修正；`test_no_earnings_tomorrow_no_push` 更名為 `test_no_earnings_today_no_push` 與新語意一致 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v --confcutdir=tests`
- 結果：542 passed / 13 errors（無 failed）
- 失敗測試：無
- Error 明細：13 個皆為 `tests/test_local_integration.py` 內使用 `finnhub_stub`（本機 `ThreadingHTTPServer` bind `127.0.0.1:0`）的既有測試，在此 sandbox 環境下 `socket.bind` 觸發 `PermissionError: Operation not permitted`——與本次改動的檔案（`scheduler_handler.py`、`prompts.py`、三個測試檔）無關，且這些測試皆與財報通知時間邏輯無關（測的是 REST endpoint 對 Finnhub HTTP 錯誤的處理）。單獨執行 `tests/test_scheduler.py` + `tests/test_watchlist_notify_flow.py` 25 個測試全數 PASSED（含新增的 `test_run_notifies_for_todays_date_not_tomorrow`）。
- 既有 suite regression：✅ 無（542 passed 與 generator 回報一致；socket bind errors 為既有環境限制，非本次改動引入）

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- **`docs/local-daily-schedule.md:96`**：本地 launchd pipeline 說明文字仍寫「推播明日財報提醒」，與修正後的「24 小時內」語意不一致。屬 op 文件、非本次改動範圍（且 memory 記錄 launchd 已永久停用，此文件本身可能已是歷史文件），非阻塞，之後若重新啟用本地排程或整理文件時一併更新即可。

### ✅ What looks good
- `_run()` 的日期邏輯改動精準對應需求描述的時區推導（台北 D 日 10:00 = UTC D 日 02:00，此時美東仍是 D-1 晚間，D 日盤前/盤後皆未發布且都在 24 小時內），且改動範圍完全侷限在 `_run()`/`_notify_local`/`_company_summary` 的參數傳遞鏈與文案，未動 `sync_earnings()`、未動 db backend 介面、未動 schema，符合 surgical change 原則。
- 新增的回歸測試不只斷言呼叫發生，還斷言呼叫的**參數值**等於 `date.today().isoformat()`（而非 `+1`），且 docstring 直接寫出「查 +1 day 會早兩天推播」的舊 bug 描述，之後若再次回歸能一眼看出測試意圖。
- grep 全 repo `src/` 未發現殘留「明日」/`tomorrow` 語意矛盾；三個受影響測試檔的日期變數改名（`TOMORROW` → `TARGET_DATE`/`today`）與新語意一致，且斷言本身的邏輯（哪些 symbol 被通知、idempotency、per-subscriber 隔離）未被弱化，只是日期常數語意修正。
- `timedelta` import 在改動後的 `scheduler_handler.py`（`sync_earnings`/`_is_stale` 仍用）與 `test_local_integration.py`（161-162 行仍用於別的測試）皆非孤兒 import，未殘留無用 import。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞項；`docs/local-daily-schedule.md` 的文案可待重啟本地排程時順手更新（非本次必要）。
