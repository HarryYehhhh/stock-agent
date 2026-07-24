# CHANGELOG

Harness 執行記錄。**最新的在上面**（reverse chronological）。

每條由 planner / evaluator 在自己跑完後追加，main Codex 不直接寫。
格式：
```
## YYYY-MM-DD hh:mm — <sprint 或 feature> — <agent> — <一句話結果>
- 連結：spec / contract / qa-report / ADR
- 驗證：Unit <result>；Integration <not required / pass / fail>；E2E <not required / pass / fail>；External Smoke <not requested / pass / fail>（blocking: <true/false>）
- 備註：循環次數、未解問題、後續工作（若有）
```

---

<!-- 新條目從這裡開始 -->

## 2026-07-24 — dev-env-file — evaluator — ✅ Pass
- 連結：docs/qa-reports/dev-env-file.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 148 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 160 passed, 0 failed；🔴 0、🟡 0；循環次數 1；未解 P0/P1 0。無正式 contract，對照需求驗收：`make dev` 加 `--env-file .env`（Makefile:20）+ `python-dotenv` 補進 dev deps（確認修復前 lockfile 確實缺此依賴）+ 新增 `tests/test_dev_env_file.py` 先重現 `KeyError` bug 再驗證 `uvicorn.Config(env_file=...)` 修法有效（已讀 uvicorn 原始碼確認此路徑與 CLI `--env-file` 等價）；diff 完全手術式，僅 Makefile/pyproject.toml/uv.lock/新測試檔；python-dotenv 正確限定在 dev group，不影響走 SSM 的 production Lambda。

## 2026-07-24 — check-bot — evaluator — ✅ Pass
- 連結：docs/qa-reports/check-bot.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 176 passed/12 errors（既有 `test_local_integration.py` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 188 passed, 0 failed（30 新測試皆針對純函式，不碰網路）；🔴 0、🟡 0（🟢 1：`probe_groq` 例外訊息截斷後印出，目前實測不含金鑰值但長期風險留意）；循環次數 1；未解 P0/P1 0。無正式 contract，對照使用者需求驗收：五項健檢（本地 server／ngrok tunnel／Telegram webhook／金鑰／Groq）皆逐項對應 PASS/FAIL/WARN 分類規則；純判斷邏輯（`classify_server_probe`／`find_ngrok_public_url`／`webhook_url_matches`／`is_placeholder`／`mask_key_status`／`webhook_info_warnings`）與 I/O 探測分離；所有網路呼叫皆設 10s timeout；`run_checks()` 雙層 try/except 確保單項失敗不中斷後續；`scripts/check-bot.sh` 無 `set -e`；exit code 規則（有 FAIL 非 0、全 PASS/WARN 為 0）已用真實 `.env` 實跑 `make check-bot` 驗證（0 FAIL、1 WARN、exit 0）；金鑰安全審查逐路徑確認 `mask_key_status`／`check_secrets`／`probe_telegram_webhook`（webhook URL 不含 bot token）／`probe_groq`（實測假金鑰觸發 401，錯誤訊息不含金鑰值）皆未洩漏原值；唯讀確認無任何 DB 寫入或 Telegram 發訊呼叫；smoke-test 用 `.env` 已核實透過 `git check-ignore` 命中 `.gitignore` 規則、未進版控。

## 2026-07-24 — sync-local-to-dynamodb — evaluator — ✅ Pass
- 連結：docs/qa-reports/sync-local-to-dynamodb.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 146 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 158 passed, 0 failed；🔴 0、🟡 1（ADR-0004 §9 記載 upload_local_store 不搬 earnings_calendar/company_summaries，本次依使用者新指示推翻，ADR 文件未同步更新註記，屬文件與現況不一致但不阻塞）；🟢 2；循環次數 1；未解 P0/P1 0。無正式 contract，對照使用者需求驗收：`NewsTable`（PK=headline 去重、GSI published-at-index 支援近期查詢、TTL）+ SchedulerFunction 補 `State: DISABLED`（未動 IAM）+ `upload_local_store.py` 擴充 earnings_calendar/company_summaries/news（Decimal 轉換與既有 DynamoDBBackend 手法一致、TTL 對齊 backend.py 常數、nullable 欄位省略寫入、冪等由 put_item overwrite-by-key 保證且有專屬測試驗證）+ `sync-dynamodb` make target 串接 run-daily.sh 第四步（風格與既有三步一致）+ moto 測試新增 test-news 表與冪等測試；排除項（market_summaries 不同步、DynamoDBBackend 不新增 news 讀取方法、不執行 make deploy）皆核實遵守；範圍手術式，僅 9 個檔案改動且逐一對應需求。

## 2026-07-24 — dynamodb-backend-pr1-sprint（follow-up：補日期區間邊界測試）— evaluator — ✅ Pass
- 連結：docs/qa-reports/dynamodb-backend-pr1-sprint.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 145 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 157 passed, 0 failed；只補 `tests/test_dynamodb_backend.py` 新增 `test_dynamodb_watchlist_earnings_date_range_boundaries`，`src/` 零改動；🔴 0、🟡 0（解掉前次 🟡：`get_chat_watchlist_earnings` 缺日期區間過濾邊界測試）；循環次數 1；未解 P0/P1 0。

## 2026-07-24 — dynamodb-backend-pr1-sprint — evaluator — ⚠️ Pass with issues
- 連結：docs/qa-reports/dynamodb-backend-pr1-sprint.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 144 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 156 passed, 0 failed；🔴 0、🟡 1（`get_chat_watchlist_earnings` 缺日期區間過濾邊界測試，contract Tests required 明確列出但未覆蓋）；循環次數 1；未解 P0/P1 0。

## 2026-07-24 — dynamodb-backend-pr1（Phase 2 PR-1）— planner — spec + contract + ADR 完成
- 連結：docs/specs/dynamodb-production-tables.md, docs/contracts/dynamodb-backend-pr1-sprint.md, docs/decisions/0004-dynamodb-production-tables.md
- 備註：細化 Phase 2 PR-1（DynamoDB 四張新表 + DynamoDBBackend 補實作 + infra/IAM + 測試）。關鍵決策：多表風格 + application-side join；watchlist symbol GSI 省略；通知冪等改 conditional put（attribute_not_exists，同 claim_update_id）；TTL earnings_calendar 45 天 / notifications 30 天 / company_summaries 30 天 / watchlist 無 TTL；float↔Decimal 轉換保 parity；7 個原 local-only 方法升格進 StorageBackend Protocol（延伸 ADR-0003）；`get_watchlist(None)` 保留 DEFAULT_WATCHLIST 過渡分支（PR-2 移除）；upload_local_store 只搬 watchlist、不搬三個快取表。明確不改 scheduler_handler / processor_handler（PR-2）、不含 news/market_summaries（PR-3）。開放邊界：cutover 當天可能一次重複通知，以切換時機規避。

## 2026-07-24 — remove-watchlist-exchange — evaluator — ✅ Pass
- 連結：docs/qa-reports/remove-watchlist-exchange.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 131 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 143 passed, 0 failed；🔴 0、🟡 0（🟢 2：DROP COLUMN migration 依賴 SQLite ≥3.35、歷史 docs 仍留 exchange 字樣但使用者已明確排除範圍）；循環次數 1；未解 P0/P1 0。驗收範圍：`db/watchlist.py`（DEFAULT_WATCHLIST 28 筆只留 symbol）、`db/backend.py`（Protocol/Local/DynamoDB 三處 `add_to_watchlist` 拿掉 exchange 參數、watchlist CREATE TABLE 移除 exchange 欄、新增 `PRAGMA table_info` 偵測 + `ALTER TABLE DROP COLUMN` migration、`_migrate_watchlist_schema` 與六個查詢方法全數移除 exchange）、`scheduler_handler.py`（兩個 prompt template、`_company_summary`、`_notify_live` 移除 exchange）、`processor_handler.py`（`/watchlist` 只顯示 symbol）；grep 覆核 `exchange`/`NASDAQ`/`NYSE` 僅剩 migration 程式碼本身與模擬舊 schema 的測試 fixture，均合理保留非殘留；新增 `test_migration_drops_exchange_column_from_existing_per_subscriber_db` 用 `PRAGMA table_info` 斷言精準覆蓋 DROP COLUMN migration 行為；改動手術式，working tree 內其餘既有未提交檔案（Makefile/local_server.py/market_summary.py 等）核對均屬其他 phase 既有工作、本次未觸碰。

## 2026-07-24 — fix-launchd-env-loading — evaluator — ✅ Pass
- 連結：docs/qa-reports/fix-launchd-env-loading.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -q` 沙盒內 130 passed/12 errors（既有 `test_local_integration.py` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 142 passed, 0 failed；🔴 0、🟡 0（🟢 3：`set -e` 力度與腳本複雜度相稱、錯誤訊息路徑為字面字串已在已知限制揭露、plist 路徑寫死屬既有 Phase 1 設計不擴大）；循環次數 1；未解 P0/P1 0。驗收範圍：新增 `scripts/run-daily.sh`（cd → `.env` 存在檢查 exit 1 → `set -a; source .env; set +a` → 三個 make target 用 `&&` 串接）修復 launchd 不 source shell profile 導致 `.env` 變數未載入、`config.py` KeyError 的 bug；`bash -n`、`plutil -lint`、`env -i` 模擬 launchd 最小環境（複製腳本、末行改 `make -n` + echo 三把 key，不打真實 API）驗證三把 key 皆載入且三步 make 組成正確、exit 0；另模擬 `.env` 缺失情境驗證 exit 1、不執行任何 make target；plist `ProgramArguments` 改為呼叫該 script，其餘欄位（WorkingDirectory/PATH/log 路徑/StartCalendarInterval/RunAtLoad）逐項比對前次 QA report 記載值皆未變動；`docs/local-daily-schedule.md` 已安裝者 unload/load 提醒位置醒目、指令與既有安裝步驟一致；改動範圍手術式，working tree 內其餘未提交檔案（Makefile/market_summary.py 等）核對均屬其他 phase 既有工作、本次未觸碰。

## 2026-07-23 — fix-local-webhook-sqs-500 — evaluator — ✅ Pass
- 連結：docs/qa-reports/fix-local-webhook-sqs-500.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 130 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 142 passed, 0 failed；🔴 0、🟡 1（`_no_op_sqs()` 併發安全性依賴「窗口內無 await + 單一 uvicorn worker」隱含前提，未在程式碼中記錄，建議補註解，非阻塞）；循環次數 1；未解 P0/P1 0。驗收範圍：`local_server.py` 新增 `_no_op_sqs()` context manager，在呼叫 `webhook_handler.handler()` 期間把 `webhook_handler_module._sqs` 換成 no-op 假物件，修復本地 `/webhook` 首次投遞因無真實 SQS queue 而 `QueueDoesNotExist` → 500 的 bug；用 `git stash` 對修復本身做雙向 mutation test（拿掉修復 → 復現 500；還原修復 → 恢復 200）證實新增回歸測試確實鎖住此 bug；新測試刻意不借用會建立真實 queue 的 `aws` fixture，準確覆蓋既有 `test_rest_webhook_accepts_valid_secret` 未能觸及的「本地無 queue」情境；`webhook_handler.py` 零改動，Lambda 生產路徑（含真實 enqueue）不受影響。

## 2026-07-23 — fix-local-webhook-500 — evaluator — ✅ Pass
- 連結：docs/qa-reports/fix-local-webhook-500.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -v` 沙盒內 129 passed/12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 141 passed, 0 failed；🔴 0、🟡 0；循環次數 1；未解 P0/P1 0。驗收範圍：`src/agent/local_server.py` 新增 `_LOCAL_LAMBDA_CONTEXT`（`SimpleNamespace`，逐一比對 `aws_lambda_powertools` 原始碼確認四屬性無多無缺）修復本地 `/webhook` 500；`git stash` 實測復現修復前 `AttributeError: 'NoneType' object has no attribute 'function_name'` 與 root cause 吻合；新增 401/200 兩測試，200 案例以 `fake_send.assert_called_once()` 排除「`_process_update` 例外被吞」假陽性風險；`webhook_handler.py` 零改動，生產路徑不受影響。

## 2026-07-23 — phase1-local-daily-schedule — evaluator — ✅ Pass
- 連結：docs/qa-reports/phase1-local-daily-schedule.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -q` 沙盒內 127 passed/12 errors（既有 socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 139 passed, 0 failed；🔴 0、🟡 0（🟢 3：`&&` 串接失敗會擋住後兩步——TASKS.md Phase 1 已定案「失敗留 log、不做告警」，非 Critical；`generate_market_summary` 既有行為固定打大盤報價非本次新增；plist 路徑寫死本機屬 Phase 1 階段性可接受）；循環次數 1；未解 P0/P1 0。驗收範圍：`push_daily_summary()`（`src/agent/market_summary.py`，訂閱者來源/錯誤處理比照 `scheduler_handler._notify_live` 既有模式）+ `Makefile daily-summary`（`.PHONY` 已補）+ `scripts/com.earnings-agent.daily.plist`（`plutil -lint` 通過、台北 08:00、WorkingDirectory/PATH/log 皆正確）+ `docs/local-daily-schedule.md`（load/unload/改時間/看 log/手動觸發，檔名與 plist label 逐字一致）；新增 4 個 `push_daily_summary` 測試案例（多訂閱者推播、無內容不發、單一失敗不中斷、無訂閱者不發），沿用既有 fixtures，無 flaky 風險。

## 2026-07-23 — phase0-webhook-switch — evaluator — ✅ Pass
- 連結：docs/qa-reports/phase0-webhook-switch.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -q` 沙盒內 123 passed/12 errors（既有 socket bind 沙盒限制，與本次改動無關）、非沙盒重跑 135 passed, 0 failed；🔴 0、🟡 0（🟢 2：不對稱行為說明、文件風格一致性，皆不要求修改）；循環次數 1；未解 P0/P1 0。驗收範圍：Makefile 新增 `set-webhook-local`/`delete-webhook`（`.PHONY` 更新、缺參數報錯風格與既有 `set-webhook` 一致、`set-webhook` 未被改動、URL 拼接 `<WEBHOOK_URL>/webhook`、`secret_token` 帶出，dry-run + 缺參數情境皆實測驗證）、`docs/deployment-flow.md` 新增本地開發 webhook 切換小節（步驟與 Makefile 指令逐字一致，另查核「本地 webhook_handler 會驗證 secret」之描述屬實）。
## 2026-07-23 — watchlist-notify-flow-verification（T3 流程 A）— evaluator — ✅ Pass
- 連結：docs/qa-reports/manual-test-watchlist-notify.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -q` 基準 124 → 最終 129 passed（sandbox 擋既有 `finnhub_stub` 12 個 socket 測試，已用 `dangerouslyDisableSandbox` 重跑）；新增 `tests/test_watchlist_notify_flow.py` 5 案例對應 spec 全部 7 條 acceptance criteria，全鏈路驅動 `_process_update → sync_earnings → handler(_notify_local)`，僅打樁 Finnhub/Groq/Telegram 三個外部邊界；冪等斷言已用 mutation test（拿掉 `mark_notified`）驗證有效；🔴 0、🟡 0（🟢 2：一處恆真斷言、建議合併 T1/T2 分支避免基準漂移）；循環次數 1；未解 P0/P1 0；本任務為 agent 自動化整合驗證，非人工手動互動；`src/` 無改動，未發現既有真 bug

## 2026-07-23 — watchlist-notify-flow-verification（T3 流程 A）— planner — spec + contract 完成
- 連結：docs/specs/watchlist-notify-flow-verification.md, docs/contracts/watchlist-notify-flow-verification.md
- 驗證：Unit not run（planner 階段，且此環境無 Bash 無法親跑 pytest）；Integration not required（交棒 generator）；E2E not required；External Smoke not requested（blocking: false）
- 備註：把 T3 流程 A 由「使用者手動互動」改造成**全自動化 e2e 整合測試**（使用者明確偏好）。產出單一新測試檔 `tests/test_watchlist_notify_flow.py` 串鏈路 `/subscribe`→`/watch`→`sync_earnings()`→notify，覆蓋 subscribe 寫入/watch 加入/sync 寫 earnings_calendar/notify 對正確 subscriber 發正確 symbols/冪等/非訂閱者或無資料不誤發。驅動方式決策：步驟1-2 直呼 `processor_handler._process_update`（真實指令路由，不用 POST /webhook 因其只 enqueue SQS）、步驟3 直呼 `sync_earnings()`、步驟4-5 直呼 `scheduler_handler.handler`（local 走 `_notify_local`）。Mock 邊界決策：Finnhub 改用 **socketless** 假 client（monkeypatch `agent.tools._http._client`）而非既有 `finnhub_stub` 真 HTTP server，避開 sandbox 對 socket bind 的限制、且不動 test_local_integration.py 既有 fixture；Groq monkeypatch `scheduler_handler.run_agent`；Telegram monkeypatch `send_message`。無 ADR（純測試建置，無不可逆架構決策）。未發現既有真 bug；若 generator 揭露則比照 insert_news 先於 QA report 標明再最小修正。基準：planner 無 Bash 無法親跑，CHANGELOG 上次記錄 124/124（早於 T1 新增測試），generator/evaluator 須先跑 `PYTHONPATH=src uv run pytest tests/ -q` 確立確切基準 N。QA report 指定檔名 `docs/qa-reports/manual-test-watchlist-notify.md`，內容須標明為自動化驗證。

## 2026-07-23 — manual-test-market-summary（T3 流程 B） — evaluator — ✅ Pass
- 連結：docs/qa-reports/manual-test-market-summary.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -q` 130/130 passed（sandbox 對本機 socket bind 有限制，`test_local_integration.py` 12 條以非 sandbox 模式重跑確認）；🔴 0、🟡 1（已修復）；循環次數 1；未解 P0/P1 0。手測涵蓋 `/market-summary`、`/news`，DB 權威查詢確認 `news`/`market_summaries` 有資料、摘要繁中且 `news_ids` 可稽核、dedup（`count(*)=count(DISTINCT headline)=54`）正確。測試中揪出 `insert_news()` 用不可靠的 `cur.rowcount` 累加 `INSERT OR IGNORE`、回傳「嘗試插入數」而非「實際新增數」的 bug，已改用 `conn.total_changes` 差值修復（`src/agent/db/backend.py`）並補 2 條迴歸測試（`tests/test_storage_backend.py`）。範圍僅涵蓋 T3 流程 B；流程 A（Telegram watchlist 財報通知）尚未測試，待補 `docs/qa-reports/manual-test-watchlist-notify.md`。

## 2026-07-22 — storage-backend-strategy-sprint — evaluator — ✅ Pass
- 連結：docs/qa-reports/storage-backend-strategy-sprint.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -q` 124/124 passed（sandbox 對 ~/.cache/uv 有寫入限制，已用非 sandbox 模式重跑）；🔴 0、🟡 0（🟢 1，Protocol `get_watchlist` 預設值與既有 `LocalSQLiteBackend` 簽名不完全對齊，非本次引入、不影響 runtime）；循環次數 1；未解 P0/P1 0

## 2026-07-22 — storage-backend-strategy-sprint — planner — spec + contract + ADR 完成
- 連結：docs/specs/storage-backend-strategy.md, docs/contracts/storage-backend-strategy-sprint.md, docs/decisions/0003-storage-backend-strategy.md
- 驗證：Unit not run（planner 階段）；Integration not required（交棒 generator）；E2E not required；External Smoke not requested（blocking: false）
- 備註：內部重構——引入 `StorageBackend`（`typing.Protocol`）+ `DynamoDBBackend` class，`get_backend()` 依 backend 名回傳對應實例（維持 `@lru_cache` singleton），收斂 11 處 `if selected_backend_name()=="local"` wrapper 分支（conversations×2/subscribers×3/dedup×1/watchlist×4/earnings×1）成單一 `get_backend().X(...)`。Scope 取捨：Protocol 只涵蓋跨 backend 共用的 11 方法，local-only 方法（news/summaries/notifications/earnings join/export）不進 Protocol、不在 DynamoDBBackend 補 stub（見 ADR-0003）。dynamodb degenerate 行為維持不變（watchlist add/remove→NotImplementedError、get_watchlist→DEFAULT_WATCHLIST、seed/upsert_earnings→no-op）。`scheduler_handler._run()` 的 local vs live-fetch 流程分支不在此 sprint 收斂（非 storage 分派）。測試面向：既有 moto 測試（test_db/test_dedup/test_webhook）即為 DynamoDBBackend 覆蓋；需修 conftest（移除已刪除的 `_table.cache_clear()`、在 autouse env fixture 每測試前清 `get_backend` 快取防跨測試洩漏）。DynamoDBBackend 建構與 no-op 方法不得觸網（test_tools 在 dynamodb 無 moto 下會走 upsert no-op）。

## 2026-07-22 — watch-symbol-validation — evaluator — ✅ Pass
- 連結：docs/qa-reports/watch-symbol-validation.md
- 驗證：Unit pass（`PYTHONPATH=src uv run pytest tests/ -v`，118/118 passed）；Integration pass（`/watch`、`/unwatch` 情境含在同一 suite）；E2E not required；External Smoke not requested（blocking: false）
- 備註：修正前次 sprint 🟡 issue——`/watch`、`/unwatch` 補上 `SYMBOL_RE.fullmatch()` regex 驗證，正確拒絕特殊字元／過長／多字參數，接受 `BRK.B`/`BF-B` 型態；新增 6 條測試。🔴 0、🟡 0；循環次數 1；未解 P0/P1：0/0。

## 2026-07-22 — per-subscriber-watchlist-sprint — evaluator — ⚠️ Pass with issues
- 連結：docs/qa-reports/per-subscriber-watchlist-sprint.md, docs/contracts/per-subscriber-watchlist-sprint.md
- 驗證：Unit pass（`PYTHONPATH=src uv run pytest tests/ -v`，110/110 passed）；Integration pass（scheduler/processor per-subscriber 情境含在同一 suite）；E2E not required；External Smoke not requested（blocking: false）
- 備註：循環次數 1；🔴 0、🟡 1（`/watch`、`/unwatch` 的「格式無效」判定僅擋空字串/含空白，未擋特殊符號等其他無效格式，非阻塞）、🟢 3；未解 P0/P1：0/0。Contract deliverable 逐條核對全數 ✅（一項標 ⚠️ 見上述 🟡）。

## 2026-07-22 — per-subscriber-watchlist-sprint — planner — spec + contract + ADR 完成
- 連結：docs/specs/per-subscriber-watchlist.md, docs/contracts/per-subscriber-watchlist-sprint.md, docs/decisions/0002-per-subscriber-watchlist-migration.md
- 驗證：Unit not run（planner 階段）；Integration not required（交棒 generator）；E2E not required；External Smoke not requested（blocking: false）
- 備註：把全域 watchlist 改為 per-subscriber junction table（PK `(chat_id, symbol)` + `idx_watchlist_symbol`），`ticker` 欄位/命名改名為 `symbol`；含既有 local DB migration（既有 subscriber 綁定其舊全域清單，見 ADR-0002）；移除 `_seed_watchlist()` 全域覆蓋改為 `/subscribe` seed-if-empty；scheduler local 改為 earnings_calendar ⋈ watchlist ⋈ subscribers per-user join；新增 `/watchlist`、`/watch`、`/unwatch` 指令。DynamoDB per-user 本 sprint 不做，僅記錄設計方向（PK=chat_id, SK=symbol + symbol GSI）。Open questions：per-subscriber summary 會線性增加 run_agent 次數（小規模可接受）。

## 2026-07-21 23:28 — groq-migration-and-market-summary — main — ✅ Pass
- 連結：無 spec/contract（與使用者直接議定後實作）；分支 `feat/groq-migration-and-market-summary`
- 驗證：Unit pass（86/86，`PYTHONPATH=src uv run pytest tests/ -q`）；Integration pass；E2E not required；External Smoke not requested（blocking: false）
- 備註：兩部分——(1) LLM 由 Google Gemini（`gemini-2.5-flash` / `google-genai`）遷移到 Groq（`llama-3.3-70b-versatile` / `groq` SDK）；同步更新 `agent.py` tool-use loop、`config.py`、`.env.example`（`GEMINI_API_KEY`→`GROQ_API_KEY`）、`Makefile ssm-put`、`pyproject.toml`/`uv.lock` 與 README/CLAUDE/AGENTS 文件。(2) 新增每日盤勢摘要 `market_summary.py`：抓 Big 7 + AI 產業英文新聞存進本地 SQLite `news` 表，再由 Groq 綜整成繁中每日 briefing 並存進 `market_summaries`（附來源 news ids 供稽核）；`backend.py` 新增 `news`/`market_summaries` 表與存取方法，`local_server.py` 新增 `/market-summary`、`/news` endpoint，新增 `tests/test_market_summary.py`。

## 2026-07-21 15:00 — earnings-notifications — main — ✅ Pass
- 連結：無 spec/contract（與使用者直接議定計劃後實作）
- 驗證：Unit pass（79/79）；Integration pass；E2E not required；External Smoke not requested（blocking: false）
- 備註：local backend 通知改為「sync 未來 30 天財報到 SQLite → watchlist ⋈ earnings_calendar join → 推播」；新增 `earnings_notifications` 冪等表、`earnings_calendar.updated_at` 與日期 index（含舊 DB migration）；Finnhub 失敗時推播帶警告並用快取資料；dynamodb（Lambda）路徑維持原 live-fetch 行為；新增 `make notify-earnings`。

## 2026-07-20 15:18 — local-first-storage-sprint — evaluator — ⚠️ Pass with issues
- 連結：`docs/specs/local-first-storage.md`, `docs/contracts/local-first-storage-sprint.md`, `docs/decisions/0001-local-first-storage-backend.md`, `docs/qa-reports/local-first-storage-sprint.md`
- 驗證：Unit pass；Integration pass；E2E not required；External Smoke not requested（blocking: false）
- 備註：循環 2 次；repo `.venv` 完整測試 55/55 通過；`make test` 因當前 PATH 缺少 `uv` 而未啟動 pytest；未解 P0/P1：0/0。
