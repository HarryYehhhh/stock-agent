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

## 2026-07-23 — watchlist-notify-flow-verification（T3 流程 A）— evaluator — ✅ Pass
- 連結：docs/qa-reports/manual-test-watchlist-notify.md
- 備註：`PYTHONPATH=src uv run pytest tests/ -q` 基準 124 → 最終 129 passed（sandbox 擋既有 `finnhub_stub` 12 個 socket 測試，已用 `dangerouslyDisableSandbox` 重跑）；新增 `tests/test_watchlist_notify_flow.py` 5 案例對應 spec 全部 7 條 acceptance criteria，全鏈路驅動 `_process_update → sync_earnings → handler(_notify_local)`，僅打樁 Finnhub/Groq/Telegram 三個外部邊界；冪等斷言已用 mutation test（拿掉 `mark_notified`）驗證有效；🔴 0、🟡 0（🟢 2：一處恆真斷言、建議合併 T1/T2 分支避免基準漂移）；循環次數 1；未解 P0/P1 0；本任務為 agent 自動化整合驗證，非人工手動互動；`src/` 無改動，未發現既有真 bug

## 2026-07-23 — watchlist-notify-flow-verification（T3 流程 A）— planner — spec + contract 完成
- 連結：docs/specs/watchlist-notify-flow-verification.md, docs/contracts/watchlist-notify-flow-verification.md
- 驗證：Unit not run（planner 階段，且此環境無 Bash 無法親跑 pytest）；Integration not required（交棒 generator）；E2E not required；External Smoke not requested（blocking: false）
- 備註：把 T3 流程 A 由「使用者手動互動」改造成**全自動化 e2e 整合測試**（使用者明確偏好）。產出單一新測試檔 `tests/test_watchlist_notify_flow.py` 串鏈路 `/subscribe`→`/watch`→`sync_earnings()`→notify，覆蓋 subscribe 寫入/watch 加入/sync 寫 earnings_calendar/notify 對正確 subscriber 發正確 symbols/冪等/非訂閱者或無資料不誤發。驅動方式決策：步驟1-2 直呼 `processor_handler._process_update`（真實指令路由，不用 POST /webhook 因其只 enqueue SQS）、步驟3 直呼 `sync_earnings()`、步驟4-5 直呼 `scheduler_handler.handler`（local 走 `_notify_local`）。Mock 邊界決策：Finnhub 改用 **socketless** 假 client（monkeypatch `agent.tools._http._client`）而非既有 `finnhub_stub` 真 HTTP server，避開 sandbox 對 socket bind 的限制、且不動 test_local_integration.py 既有 fixture；Groq monkeypatch `scheduler_handler.run_agent`；Telegram monkeypatch `send_message`。無 ADR（純測試建置，無不可逆架構決策）。未發現既有真 bug；若 generator 揭露則比照 insert_news 先於 QA report 標明再最小修正。基準：planner 無 Bash 無法親跑，CHANGELOG 上次記錄 124/124（早於 T1 新增測試），generator/evaluator 須先跑 `PYTHONPATH=src uv run pytest tests/ -q` 確立確切基準 N。QA report 指定檔名 `docs/qa-reports/manual-test-watchlist-notify.md`，內容須標明為自動化驗證。

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
