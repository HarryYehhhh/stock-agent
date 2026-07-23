# Spec: 流程 A（Telegram watchlist 財報通知，local 模式）自動化 e2e 驗證

對應任務：`docs/TASKS.md` T3「流程 A」。原 T3 設計為「使用者手動互動 + Telegram + Finnhub 手打」；本 spec 把它改造成**全自動化整合測試**，agent 自己寫測試驗整條鏈路，不要求使用者手動互動或手打 API（使用者明確偏好）。

## Goal
用一組自動化整合測試，把 local backend（SQLite）下的財報通知鏈路「`/subscribe` → `/watch` → `sync_earnings()` → notify」端到端串起來驗證，並鎖住冪等與 per-subscriber 隔離行為，所有外部邊界 mock，不打真實服務。

## 背景：流程 A 鏈路（local backend / SQLite）
1. `/subscribe` → `subscribe()` 寫入 subscribers 表 + `seed_watchlist_if_empty()` seed 預設 watchlist
2. `/watch <SYMBOL>` → `add_to_watchlist()` 加入該 chat_id 的 watchlist
3. `sync_earnings()`（入口 `make sync-earnings` / `python -m agent.scheduler_handler sync`）→ 對「所有 active 訂閱者 watchlist 的 distinct symbols」抓 Finnhub 近 30 天財報 → 經 `earnings.get_earnings_calendar()` → `upsert_earnings_calendar()` 寫入 SQLite `earnings_calendar`
4. notify（`scheduler_handler.handler` → local backend 走 `_notify_local`）→ 從 DB 讀「明天」的 watchlist 財報、per-subscriber join、產公司摘要（`company_summaries` 快取）、`send_message` 推播、`mark_notified` 寫入 `earnings_notifications` 去重
5. 冪等：重跑 notify 不重複發送同一 `(chat_id, symbol, earning_date)`，也不重複呼叫 Groq

## User stories
- As a maintainer, I want 一組自動化測試把流程 A 整條鏈路跑通, so that 我不必每次改動都手動開 `make dev` + Telegram + Finnhub 驗證。
- As a maintainer, I want 冪等與 per-subscriber 隔離被測試鎖住, so that 未來重構 scheduler/backend 時回歸能立即發現破壞。

## Acceptance criteria
- [ ] **subscribe 寫入**：`/subscribe` 後 subscribers 表有該 chat_id 且 active=1，且 watchlist 被 seed（有列）。
- [ ] **watch 加入**：`/watch <SYMBOL>` 後該 chat_id 的 watchlist 含該（大寫）symbol。
- [ ] **sync 寫入 earnings_calendar**：`sync_earnings()` 執行後，SQLite `earnings_calendar` 出現 mock Finnhub 回傳的、日期為「明天」的 watchlist symbol 財報列。
- [ ] **notify 發正確 symbols 給正確 subscriber**：notify 後 `send_message` 對正確 chat_id 送出，body 只含該 subscriber watchlist 中「明天有財報」的 symbol，不含他人 symbol 或無財報 symbol。
- [ ] **冪等**：連跑兩次 notify，`send_message` 與 `run_agent` 總呼叫次數不因第二次而增加；`earnings_notifications` 對同一 `(chat_id, symbol, earning_date)` 只有一列。
- [ ] **非訂閱者不誤發**：已 `/unsubscribe`（active=0）的 chat_id 不會被 sync 抓、不會被 notify 推播。
- [ ] **無資料不誤發**：明天沒有任何 watchlist 財報時，notify 不呼叫 `run_agent`、不呼叫 `send_message`。
- [ ] 全數新測試不打真實 Finnhub / Groq / Telegram；既有測試全綠且數量不減。

## Non-goals
- 不驗 production / DynamoDB 路徑（`add_to_watchlist` 拋 `NotImplementedError`、`upsert_earnings_calendar` no-op、scheduler 走 `_notify_live`）——那是 T5。
- 不驗流程 B（每日科技股摘要 `/news`、`/market-summary`）——那有獨立 QA report。
- 不新增/修改應用程式行為（scheduler / backend / processor）。僅在測試揭露既有真 bug 時，先於 QA report 標明再做最小修正（比照先前 `insert_news` 的處理方式）。
- 不驗真實 Telegram 送達、真實 Finnhub 回應內容、真實 Groq 生成品質。
- 不做 `sync_earnings` 的 CLI 入口（`__main__` `sys.argv` 分支）測試——本 spec 驗函式層鏈路，不驗 CLI 包裝。

## 驅動方式決策（planner 指定）
- **步驟 1–2**：直接呼叫 `processor_handler._process_update({"message": {"chat": {"id": <id>}, "text": <cmd>}})`。理由：走真實指令路由（subscribe+seed、/watch 驗證+add）與真實 DB 寫入，確定性高、無 SQS/HTTP 開銷；`tests/test_processor_handler.py`、`tests/test_local_integration.py` 已有相同先例。**不用** POST `/webhook`：webhook handler 只 enqueue 到 SQS、不跑 processor，無法驅動後續處理。
- **步驟 3**：直接呼叫 `scheduler_handler.sync_earnings()`，讓真實 `earnings.get_earnings_calendar()` → 真實 `upsert_earnings_calendar()` 執行並寫 SQLite；只 mock Finnhub 的 httpx 邊界。
- **步驟 4–5**：直接呼叫 `scheduler_handler.handler({}, CTX)`（local backend 由 `local_store` fixture 設定，`_run()` 會路由到 `_notify_local`）。

## 外部邊界 mock 策略決策（planner 指定）
- **Finnhub**：monkeypatch `agent.tools._http._client`（module 級 singleton）為一個 socketless 假 client，其 `.get(path, params)` 依 `params["symbol"]` 回傳 `{"earningsCalendar": [...]}`（含 `.raise_for_status()` no-op、`.json()`）。理由：`_http.client()` 讀 `_client` global，直接替換即可讓真實工具跑完 filter+upsert，**不綁 socket**，避開 sandbox「本地 socket bind 限制」；比既有 `finnhub_stub`（真 `ThreadingHTTPServer`）更穩、更快，且新測試不依賴 socket 環境。
- **Groq**：monkeypatch `scheduler_handler.run_agent`（`_company_summary` 呼叫的參照）為 `MagicMock`，回傳固定摘要字串。
- **Telegram**：monkeypatch `scheduler_handler.send_message` 與 `processor_handler.send_message` 為 `MagicMock`。

## 基準測試數
- Planner 於此環境**無 Bash，無法親跑** `pytest`。
- 現況盤點：`tests/` 13 個檔案共 120 個 `def test_`；含 `@pytest.mark.parametrize` 展開後 collected 數更高。CHANGELOG 最後記錄基準為 **124/124（2026-07-22）**，該數字**早於** T1（`test(local-server): add integration tests for /market-summary and /news`，commit 55c4312）新增的整合測試，故目前實際基準應 > 124。
- generator/evaluator 必須先跑 `PYTHONPATH=src uv run pytest tests/ -q` 確立目前確切基準 N（sandbox 若擋 `~/.cache/uv` 寫入或 socket bind，用 `dangerouslyDisableSandbox` 重跑），再驗「N + 新測試」全綠。

## Open questions
- 無阻擋性問題。若 generator 在實作時發現 `_notify_local` 或 backend 有既有真 bug，依 non-goals 規則先於 `docs/qa-reports/manual-test-watchlist-notify.md` 標明再最小修正。
