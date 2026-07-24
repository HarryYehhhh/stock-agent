# QA Report: dynamodb-downsync-sprint

- **Contract**: docs/contracts/dynamodb-downsync-sprint.md
- **Date**: 2026-07-24
- **Verdict**: ✅ Pass

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| A. 新增 `agent.db.download_remote_store`，CLI `--write`/dry-run 對稱於 `upload_local_store` | ✅ | `download_remote_store.py:25-34`，flag 慣例與 `upload_local_store._parser()` 一致 |
| A. 目標路徑用 `local_store_path()` | ✅ | `download_remote_store.py:22,59` |
| A. 全量 scan 進記憶體，正確跟進 `LastEvaluatedKey` | ✅ | `_scan_all`（`download_remote_store.py:37-44`）；`test_scan_all_paginates` 驗證兩頁皆取回 |
| A. 兩表 scan 全成功後才單一 transaction DELETE+INSERT+commit | ✅ | `main()` 先 `_scan_all` 兩表、皆成功才呼叫 `_overwrite_local_store`；後者用 `with backend._connect() as conn:`（sqlite3 連線 context manager：正常結束自動 commit、例外自動 rollback）一次做完兩表 DELETE+INSERT |
| A. 型別轉換（active Bool→INTEGER、position→INTEGER、subscribed_at 回填） | ✅ | `_watchlist_row`/`_subscriber_row`（`download_remote_store.py:47-53`）；`test_downsync_subscribers_active_type_conversion`、`test_downsync_subscriber_missing_subscribed_at_backfills_now` 驗證 |
| A. 只覆蓋 watchlist/subscribers，不觸碰其他表 | ✅ | 模組完全不 import/引用 conversations/dedup/earnings_calendar/company_summaries/news/market_summaries 的 table 存取；`test_downsync_does_not_touch_other_local_tables` 驗證 |
| A. dry-run 只印 count、不動本地、exit 0 | ✅ | `main()` `if not args.write: return 0`；`test_downsync_dry_run_default_does_not_write` |
| A. scan 例外 → 動本地寫入前中止、stderr、非 0 exit | ✅ | `main()` 的 try/except 包住兩次 `_scan_all`，失敗直接 `return 1`，尚未進入 write 分支；`test_downsync_scan_failure_aborts_before_local_write` |
| A. transaction 內例外 → rollback，非空非半空，非 0 exit | ✅ | `_overwrite_local_store` 例外由 `with conn:` 自動 rollback；`test_downsync_insert_failure_rolls_back_and_preserves_local_store` 用 monkeypatch 使 `_subscriber_row` 在 DELETE 之後、INSERT 途中拋錯，驗證兩表皆回到覆蓋前狀態 |
| B. `_write_rows` 移除 conversations/subscribers/dedup/watchlist 上傳迴圈 + dry-run count 如實反映 | ✅ | `upload_local_store.py` diff 精準移除對應 4 個 put_item 迴圈與 print 欄位 |
| B. 保留 earnings_calendar/company_summaries/news 上傳不變 | ✅ | diff 顯示這三段完全未改動 |
| B. 移除 orphan import | ✅ | `conversations_table`/`processed_updates_table`/`subscribers_table`/`watchlist_table` 四個 import 一併移除，其餘 import 不變 |
| C. `make sync-from-dynamodb` + `.PHONY` | ✅ | `Makefile:1,39-40` |
| C. `scripts/run-downsync.sh`（對齊 run-daily.sh 風格） | ✅ | cd → `.env` 存在檢查 exit 1 → `set -a; source .env; set +a` → `make sync-from-dynamodb`；結構與 `run-daily.sh` 逐行對應，只換了最後一步 |
| C. `scripts/com.earnings-agent.downsync.plist`（00:00 台北、對齊既有 plist 風格、`plutil -lint` 過） | ✅ | `plutil -lint` 通過；與 `com.earnings-agent.daily.plist` 逐 key 比對一致（Label/ProgramArguments/WorkingDirectory/PATH/StandardOut·ErrorPath/RunAtLoad=false），僅 Hour/Minute 改為 0/0 |
| C. 文件更新（00:00 下行小節 + 上行描述更正） | ✅ | `docs/local-daily-schedule.md` 新增完整 00:00 小節（安裝/解除/看 log/手動觸發），10:30 小節上行描述已改為只述 earnings_calendar/company_summaries/news |
| Out of scope 遵守（不改 seed_watchlist_if_empty、不下行 conversations/dedup、不啟用雲端 EventBridge/deploy、不改 infra、不改 Backend 既有方法語意） | ✅ | 全部核實：`git diff --stat` 未觸及 `infra/`、`seed_watchlist_if_empty`、`backend.py` 既有方法簽章/邏輯 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：188 passed / 12 errors（`tests/test_local_integration.py` 既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）
- 非沙盒重跑（`dangerouslyDisableSandbox: true`）：**200 passed, 0 failed**（191 既有 + 9 新增於 `test_download_remote_store.py`，`test_upload_local_store.py` 既有 3 個測試改為回歸斷言）
- 失敗測試：無
- 既有 suite regression：✅

### 額外驗證（超出「跑測試」的逐項確認）

1. **上行回歸測試對舊 code 確實會 fail**：`git stash push -- src/agent/db/upload_local_store.py` 後單獨重跑 `tests/test_upload_local_store.py`，`test_upload_write_copies_rows_to_dynamodb` 如預期 fail（`subscribers/watchlist/conversations/dedup` 计数不為 0），其餘 6 個測試仍 pass；`git stash pop` 後確認全部復原、200 passed 依舊成立。
2. **`plutil -lint scripts/com.earnings-agent.downsync.plist`**：OK。
3. **手動核對** `download_remote_store.py` 未 import 任何非 watchlist/subscribers 的 table accessor，靜態確認「下行不越界」在程式碼層級也成立（非僅測試層級巧合）。

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- **`src/agent/db/download_remote_store.py:56-70`**：`_overwrite_local_store` 每次呼叫都 `LocalSQLiteBackend(local_store_path())` 建新實例（會觸發 `_init_schema()` 全表 `CREATE TABLE IF NOT EXISTS` 檢查）。功能上無害（冪等、開銷極小），純粹想指出：若之後 `main()` 需要在同一進程內多次呼叫，可考慮重用同一 backend 實例；目前用法（CLI 單次執行）不構成問題，不需要改。

## What looks good
- Atomicity 設計簡潔正確：善用 `sqlite3.Connection` 的 context manager 語意（正常結束自動 commit、例外自動 rollback），未额外造輪子，且與 contract「單一 transaction」要求精確對應。
- 失敗語意分層清楚：scan 失敗（寫入前）與 transaction 失敗（寫入中）分屬不同 try/except，各自對應不同的「不留半空表」保證來源（前者是順序保證，後者是 rollback 保證），測試也分別覆蓋兩種路徑，不是只測其中一種就宣稱涵蓋。
- 上行回歸測試（`test_upload_write_copies_rows_to_dynamodb`）明確標註「此測試在改動前會 fail」，並已用 `git stash` 實測驗證屬實，不是空話。
- 診斷測試涵蓋面完整對應 contract 的 8 項 Tests required（刪除傳播/型別轉換/回填/rollback/scan 中止/pagination/dry-run/冪等/不越界），一一存在對應測試且斷言具體（非泛泛 truthy 檢查）。
- diff 手術式：`upload_local_store.py` 的改動行數與移除的 orphan import 精確對應被刪除的 4 個上傳迴圈，未觸及 earnings_calendar/company_summaries/news 段落一行。
- `run-downsync.sh` 與 `com.earnings-agent.downsync.plist` 与既有 `run-daily.sh`/`com.earnings-agent.daily.plist` 逐行/逐 key 風格一致，僅在必要處做語意調整（時間、腳本名、log 檔名），未夾帶無關改動。
- 文件更新完整涵蓋新排程的安裝/解除/log/手動觸發四段，且更正了舊有「10:30 上行含 watchlist」的過時描述，避免文件與程式碼行為不一致。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻擋性問題，可直接進入下一階段（commit / push / PR）。
