# Contract: 流程 A watchlist 財報通知 e2e 驗證

對應 spec: docs/specs/watchlist-notify-flow-verification.md
對應任務: docs/TASKS.md T3「流程 A」

## 前置：先確立基準
- [ ] 跑 `PYTHONPATH=src uv run pytest tests/ -q`，記下目前通過數 N（全綠）。sandbox 若擋 `~/.cache/uv` 寫入或 socket bind，用 `dangerouslyDisableSandbox` 重跑並在 QA report 註明。
- [ ] 完成後再跑一次，須為 `N + 新增測試數` 全綠、既有測試一個都不減少。

## Deliverables

### D1 — 新增 e2e 整合測試檔 `tests/test_watchlist_notify_flow.py`
一個檔案，涵蓋流程 A 全鏈路。所有測試用 conftest 既有 `local_store` fixture（設 `STORAGE_BACKEND=local` + tmp SQLite），並在檔內定義：
- `CTX`：`SimpleNamespace(function_name=..., memory_limit_in_mb=128, invoked_function_arn=..., aws_request_id=...)`（比照 `tests/test_local_integration.py` 的 `CTX`）。
- `_process(chat_id, text)` helper：呼叫 `processor_handler._process_update({"message": {"chat": {"id": chat_id}, "text": text}})`。
- 一個 socketless 假 Finnhub client fixture（見 D2）。
- 於各測試 monkeypatch `scheduler_handler.send_message`、`scheduler_handler.run_agent`、`processor_handler.send_message` 為 `MagicMock`（`run_agent` 回傳固定摘要，如 `"*{SYMBOL}* 財報摘要"`；per-subscriber 測試用 `side_effect` 依 prompt 內容回不同摘要，比照 `tests/test_scheduler.py::test_local_per_subscriber_push_only_own_symbols`）。

### D2 — socketless 假 Finnhub client
- [ ] monkeypatch `agent.tools._http._client` 為假 client（**不要**用真 HTTP server / socket）。
- [ ] 假 client 的 `.get(path, params)` 依 `params.get("symbol")` 回傳一個 response-like 物件：`.raise_for_status()` no-op、`.json()` 回 `{"earningsCalendar": [rows]}`。
- [ ] rows 由測試註冊：`{symbol: [ {"symbol": symbol, "date": <明天 ISO>, "hour": "amc", "epsEstimate": ..., "revenueEstimate": ...} ]}`；未註冊的 symbol 回 `{"earningsCalendar": []}`。
- [ ] 「明天」用 `(date.today() + timedelta(days=1)).isoformat()`（與 `scheduler_handler._run()` 的 tomorrow 對齊，落在 `sync_earnings` 的 today..today+30 視窗內）。
- 注意：`earnings.get_earnings_calendar()` 會對回傳做 client-side symbol filter 並呼叫 `upsert_earnings_calendar()`，故假 client 只要回對應 symbol 的列即可；真實 upsert 會實際寫 SQLite。

### D3 — 測試案例（逐一對應驗收點）
每個案例都是**全鏈路**（透過真實指令路由 + 真實 sync + 真實 notify），而非重測已有單元。至少涵蓋：

- [ ] **`test_full_chain_subscribe_watch_sync_notify`** → 覆蓋 subscribe 寫入 + watch 加入 + sync 寫入 + notify 發正確 symbol
  - `_process("111", "/subscribe")`；`_process("111", "/watch PLTR")`（PLTR 不在 `DEFAULT_WATCHLIST`，凸顯自訂 /watch 進鏈路）。
  - 假 Finnhub 只為 `PLTR` 註冊明天財報（其餘 seeded 預設 symbol 回空）。
  - `scheduler_handler.sync_earnings()`。
  - 直接查 tmp SQLite 斷言：`subscribers` 有 `("111", active=1)`；`watchlist` 含 `("111","PLTR")`；`earnings_calendar` 有 `PLTR` 且 `earning_date == 明天`。
  - `scheduler_handler.handler({}, CTX)`；斷言 `send_message` 呼叫一次、chat_id=="111"、body 含 PLTR 摘要且不含其他 symbol；`earnings_notifications` 有一列 `("111","PLTR",明天)`。

- [ ] **`test_notify_rerun_is_idempotent`** → 覆蓋冪等
  - 同上 setup 跑到 notify 一次後，再跑 `scheduler_handler.handler({}, CTX)` 一次。
  - 斷言 `send_message.call_count` 與 `run_agent.call_count` 皆維持 1（第二次不增加）；`earnings_notifications` 對 `("111","PLTR",明天)` 仍只有一列。

- [ ] **`test_per_subscriber_gets_only_own_watched_symbols`** → 覆蓋 notify 對正確 subscriber 發正確 symbols
  - `_process("111","/subscribe")` 後把 111 清成只追 AAPL（或直接 `_process("111","/watch AAPL")` 並確保只驗 AAPL 出現/NVDA 不出現，seed 的其餘 symbol 因無財報不進 body）；`_process("222","/subscribe")` + 只讓 222 追 NVDA。
  - 假 Finnhub 為 AAPL、NVDA 各註冊明天財報。`run_agent` 用 `side_effect` 依 prompt 是否含 "NVDA" 回不同摘要。
  - sync + notify 後斷言：送給 "111" 的 body 含 AAPL、不含 NVDA；送給 "222" 的 body 含 NVDA、不含 AAPL。
  - 註：若採 /subscribe seed 全預設清單，需確保只有 AAPL / NVDA 有註冊財報，其餘預設 symbol 回空故不進 body；斷言聚焦 AAPL vs NVDA 的互斥即可。

- [ ] **`test_unsubscribed_user_not_notified`** → 覆蓋非訂閱者不誤發
  - `_process("111","/subscribe")` + `_process("111","/watch AAPL")` + `_process("111","/unsubscribe")`。
  - 假 Finnhub 為 AAPL 註冊明天財報。sync + notify。
  - 斷言：`send_message` 未被呼叫、`run_agent` 未被呼叫。（`get_active_watchlist_symbols` / `get_watchlist_earnings_for_date` 都以 active=1 過濾。）

- [ ] **`test_no_earnings_tomorrow_no_push`** → 覆蓋無資料不誤發
  - `_process("111","/subscribe")` + `_process("111","/watch AAPL")`，但假 Finnhub 對所有 symbol 回空（或只註冊一個非明天日期的財報）。
  - sync + notify。斷言 `run_agent`、`send_message` 皆未被呼叫。

> 若上述任一案例揭露 `_notify_local` / backend / processor 的既有真 bug：先在 `docs/qa-reports/manual-test-watchlist-notify.md` 標明（重現步驟 + 根因），再做**最小**修正（surgical），不順手改其他程式。

## Out of scope
- production / DynamoDB 路徑（T5）。
- 流程 B（/news、/market-summary）。
- 真實 Finnhub / Groq / Telegram 呼叫。
- `sync_earnings` 的 `__main__` CLI 分支測試。
- 既有 `tests/test_local_integration.py` 的 `finnhub_stub`（真 HTTP server）重構或搬遷——本合約新測試改用 socketless mock（D2），不動既有檔的 fixture，以免波及既有 15 個測試。

## Tests required
- [ ] D3 全部案例通過（對應 spec 全部 Acceptance criteria）。
- [ ] 新測試皆不打真實 Finnhub / Groq / Telegram（用 monkeypatch / socketless fake，沿用 `local_store` fixture）。
- [ ] 整合斷言直接查 tmp SQLite（`sqlite3.connect(local_store)`）驗 `subscribers` / `watchlist` / `earnings_calendar` / `earnings_notifications` 內容——比照 `tests/test_local_integration.py` 既有查表模式。

## Definition of done
- 全部 deliverable 打勾。
- `PYTHONPATH=src uv run pytest tests/ -q` 全綠，通過數 = 前置基準 N + 新增測試數；既有測試無一被移除或改壞。
- 若 sandbox 擋 uv cache / socket，用 `dangerouslyDisableSandbox` 重跑並於 QA report 註明基準 N 與最終數。
- evaluator 產出 QA report 至 **`docs/qa-reports/manual-test-watchlist-notify.md`**（沿用 T3 檔名，但 report 內容須標明「本任務為 agent 自動化整合驗證，非人工手動互動」）；QA report 無 P0/P1。
- 若過程修了既有真 bug，QA report 需含 bug 說明與修正 diff 摘要。
