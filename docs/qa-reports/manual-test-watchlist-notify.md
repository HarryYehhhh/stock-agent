# QA Report: watchlist-notify-flow-verification（T3 流程 A）

> **本任務為 agent 自動化整合驗證，非人工手動互動。** 原 T3 設計為「使用者手動 Telegram + Finnhub 手測」，依 spec 決策改為全自動化整合測試（`tests/test_watchlist_notify_flow.py`），由 generator 撰寫、evaluator（本報告）驗收。使用者全程未手動操作 Telegram / Finnhub。

- **Contract**: docs/contracts/watchlist-notify-flow-verification.md
- **Spec**: docs/specs/watchlist-notify-flow-verification.md
- **Date**: 2026-07-23
- **Verdict**: ✅ Pass

## 基準與最終測試數

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -q`
- Sandbox 限制：`tests/test_local_integration.py` 內 12 個測試起 `ThreadingHTTPServer` 綁 socket（既有 `finnhub_stub` fixture），sandbox 內 `bind()` 拋 `PermissionError: [Errno 1] Operation not permitted`。在 sandbox 內執行得到 `117 passed, 12 errors`；改用 `dangerouslyDisableSandbox: true` 重跑後，這 12 個測試正常通過。
- 基準 N（暫移出 `tests/test_watchlist_notify_flow.py` 後，`dangerouslyDisableSandbox` 下重跑全套）：**124 passed**。與 `docs/CHANGELOG.md` 最近一次記錄的基準（2026-07-22 storage-backend-strategy-sprint，124/124）一致。
  - 補充說明：目前 worktree 所在的 `main` 分支（`a50a99e`）**尚未包含** T1 新增的 `/market-summary`、`/news` 整合測試（commit `55c4312`／`8849549`，位於 `chore/db-first-followups-t1-t2` 分支，尚未合併回 `main`）。因此本次確立的實際基準確實是 124，而非 spec 猜測的「應 > 124」；此為分支狀態差異，非本次測試新增的問題，不影響驗收。
- 最終數（含 `tests/test_watchlist_notify_flow.py` 5 個新案例，`dangerouslyDisableSandbox` 下）：**129 passed**（= 124 + 5），既有測試一個未減、未改。
- `tests/test_watchlist_notify_flow.py` 單獨執行：5 passed（不需 `dangerouslyDisableSandbox`，因為新測試改用 socketless mock，不綁真實 socket）。
- `src/` 無任何改動（`git diff --stat -- src/` 為空），符合 spec non-goals「不新增/修改應用程式行為」、也未發現既有真 bug。

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| D1 — 新增 `tests/test_watchlist_notify_flow.py`，內含 `CTX`、`_process()`、假 Finnhub fixture、於各測試 monkeypatch 三個外部邊界 | ✅ | 逐項核對：`CTX` 為 `SimpleNamespace(function_name, memory_limit_in_mb=128, invoked_function_arn, aws_request_id)`；`_process()` 呼叫 `processor_handler._process_update`；`_mocks()` helper 統一 monkeypatch `agent.tools._http._client`、`processor_handler.send_message`、`scheduler_handler.send_message`、`scheduler_handler.run_agent` |
| D2 — socketless 假 Finnhub client | ✅ | `_FakeFinnhubClient.get(path, params)` 依 `params.get("symbol")` 回傳 `_FakeResponse`（`.raise_for_status()` no-op、`.json()` 回 `{"earningsCalendar": [...]}`）；未註冊 symbol 回空 list；`TOMORROW = (date.today() + timedelta(days=1)).isoformat()` 與 `scheduler_handler._run()` 的 tomorrow 算法一致；`monkeypatch.setattr(tools_http, "_client", fake_finnhub)` 直接替換 module-level singleton，繞過 `client()` 的 lazy-init 判斷（`_client is None`），不建立任何真實 socket |
| D3 — 5 個測試案例逐一對應驗收點 | ✅ | 見下方逐案分析，皆為全鏈路（真實指令路由 + 真實 `sync_earnings()` + 真實 `scheduler_handler.handler()`），非重測既有單元 |
| Out of scope 遵守（不動 production/DynamoDB 路徑、不動流程 B、不打真實服務、不動既有 `finnhub_stub`、不測 CLI `__main__` 分支） | ✅ | 檢查 `git diff --stat -- src/` 為空；`tests/test_local_integration.py` 未被改動（其 12 個既有 socket 測試維持原樣，`dangerouslyDisableSandbox` 下全過） |
| Tests required（不打真實外部服務、直接查 tmp SQLite） | ✅ | 5 案例皆用 `sqlite3.connect(local_store)` 或間接透過 `scheduler_handler`/`processor_handler` 對 SQLite backend 讀寫做斷言，比照 `tests/test_local_integration.py` 既有模式 |
| Definition of done：全套 = 基準 N + 新增數、既有測試無一被移除或改壞 | ✅ | 124 → 129，差值 5 精確對應新增測試數 |
| QA report 檔名與內容標明「自動化整合驗證」 | ✅ | 本檔 `docs/qa-reports/manual-test-watchlist-notify.md`，開頭已標明 |
| 若揭露既有真 bug 需先標明再最小修正 | N/A | 本次驗證過程未發現 `_notify_local` / backend / processor 既有真 bug（見下方驗證方法） |

## D3 案例逐一核對（是否真覆蓋 spec 5 個 acceptance criteria、鏈路是否為全鏈路、mock 邊界是否完整）

1. **`test_full_chain_subscribe_watch_sync_notify`** — 覆蓋「subscribe 寫入」「watch 加入」「sync 寫入 earnings_calendar」「notify 發正確 symbol」四項 acceptance criteria。
   - 走 `_process("111","/subscribe")` → 真實 `subscribe()` + `seed_watchlist_if_empty()`；`_process("111","/watch PLTR")` → 真實 `add_to_watchlist()`（PLTR 不在 `DEFAULT_WATCHLIST`，確實驗到自訂 symbol 進鏈路，而非只驗預設清單）。
   - `scheduler_handler.sync_earnings()` 是真實函式，內部呼叫真實 `get_earnings_calendar()` → 真實 `upsert_earnings_calendar()`；只有 Finnhub httpx client 邊界被替換。中段直接開 `sqlite3.connect(local_store)` 查 `subscribers`、`watchlist`、`earnings_calendar` 三張表的實際內容，而非只斷言 mock 呼叫次數 —— 這是本檔測試品質的一個亮點，確實驗證資料真的落地到 SQLite。
   - 尾段 `scheduler_handler.handler({}, CTX)` 是真實 handler，內部路由到真實 `_notify_local()`；斷言 `send_message` 呼叫一次、chat_id、body 含 PLTR 不含 AAPL，並再次查 `earnings_notifications` 表確認去重紀錄真的寫入。

2. **`test_notify_rerun_is_idempotent`** — 覆蓋「冪等」。
   - 連跑 `scheduler_handler.handler({}, CTX)` 兩次，斷言 `send_message`/`run_agent` 呼叫次數維持 1、`earnings_notifications` 對 `("111","PLTR",TOMORROW)` 仍只有一列。
   - **已用 mutation test 驗證此斷言有效、非空鞘測試**：暫時把 `_notify_local()` 中的 `backend.mark_notified(...)` 註解掉重跑本案例，`fake_send.call_count` 在第二次 `handler()` 後變成 2，斷言 `== 1` 正確失敗（`AssertionError: assert 2 == 1`）；還原後回歸正常通過。證明此測試確實鎖住了 `unnotified_symbols` + `mark_notified` 的去重邏輯，而不是巧合通過。

3. **`test_per_subscriber_gets_only_own_watched_symbols`** — 覆蓋「notify 發正確 symbols 給正確 subscriber」的互斥面向。
   - 111、222 各自 `/subscribe` 後用 `_only_watch()` helper（移除 `DEFAULT_WATCHLIST` 中除目標 symbol 外的所有項目）把兩人的 watchlist 分別收斂成僅 AAPL / 僅 NVDA。`sync_earnings()` 走 `get_active_watchlist_symbols()`（distinct across active subscribers）真實只拉 AAPL、NVDA 兩檔。
   - `run_agent` 用 `side_effect` 依 prompt 是否含 "NVDA" 回不同摘要，比照 spec 指定的 `tests/test_scheduler.py::test_local_per_subscriber_push_only_own_symbols` 模式。
   - 斷言送給 111 的 body 含 AAPL 摘要且不含 "NVDA"、送給 222 的 body 含 NVDA 摘要且不含 "AAPL"，走的是真實 `get_watchlist_earnings_for_date()` 的 `watchlist ⋈ earnings_calendar ⋈ subscribers` JOIN，而非人工模擬分組，確實驗證了 DB 層 per-subscriber 隔離。

4. **`test_unsubscribed_user_not_notified`** — 覆蓋「非訂閱者不誤發」。
   - `/subscribe` → `/watch AAPL` → `/unsubscribe`，`sync_earnings()` 因 `get_active_watchlist_symbols()` 以 `active=1` join 過濾，該使用者已無法被抓進同步（`earnings_calendar` 對 AAPL 不會有列），notify 因而不呼叫 `run_agent`/`send_message`。此設計正確驗證了「取消訂閱者連 sync 都不會抓、更不會被 notify」，是比 spec 描述更嚴格的驗證（spec 只要求 notify 不誤發，此測試連同 sync 一起鎖住了 `active` 過濾在兩個環節都生效）。

5. **`test_no_earnings_tomorrow_no_push`** — 覆蓋「無資料不誤發」。
   - 不對任何 symbol 註冊假 Finnhub 資料，`sync_earnings()` 執行後 `earnings_calendar` 仍為空，`_notify_local()` 因 `get_watchlist_earnings_for_date()` 回空 list 提早 return，`run_agent`/`send_message` 皆未呼叫。

**結論**：5 案例合起來精確覆蓋 spec 的全部 7 條 acceptance criteria（subscribe 寫入、watch 加入、sync 寫入、notify 正確發送、冪等、非訂閱者不誤發、無資料不誤發），且每一案例都驅動真實的 `_process_update → sync_earnings → handler(_notify_local)` 鏈路，只在最外圍（Finnhub httpx client、Groq `run_agent`、Telegram `send_message`）打樁，符合 spec「外部邊界 mock 策略決策」。

## Mock 策略核對

- **Finnhub**：`monkeypatch.setattr(tools_http, "_client", fake_finnhub)` 直接替換 `agent.tools._http` 模組層級的 `_client` 全域變數，讓 `client()` 的 `if _client is None` 判斷短路、回傳假 client，不建立任何 socket。真實的 `get_earnings_calendar()`（含 client-side symbol filter + `upsert_earnings_calendar()` 寫入）照常執行。
- **Groq**：`monkeypatch.setattr(scheduler_handler, "run_agent", fake_run_agent)`，命中 `_company_summary()` 對 `run_agent` 的參照（`from agent.agent import run_agent` 在 `scheduler_handler` 模組內），patch 路徑正確。
- **Telegram**：`processor_handler.send_message` 與 `scheduler_handler.send_message` 分別 patch（兩個模組各自 `from agent.telegram_client import send_message` 匯入各自的模組級參照），避免只 patch 一處漏測。
- 三個邊界皆確認未觸及真實網路（`_FakeFinnhubClient` 無 socket、`run_agent`/`send_message` 皆為 `MagicMock`）。

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- **`tests/test_watchlist_notify_flow.py:98`**：`assert scheduler_handler.sync_earnings() == []` 在 `test_unsubscribed_user_not_notified` 中恆真（因為 `get_active_watchlist_symbols()` 對已 unsubscribe 的使用者回傳空 list，`sync_earnings()` 迴圈根本不會執行，`== []` 對「有 symbol 但同步失敗」與「沒有 symbol 可同步」兩種情境都會通過）。目前案例意圖是驗證「不誤同步」，語意上與 `test_full_chain_subscribe_watch_sync_notify` 裡「同步成功、無失敗」的用法不同，但不影響本次驗收結論（不算誤導性斷言，只是弱化了該行的訊號強度）。無需在本 sprint 內修正。
- 分支狀態（`chore/db-first-followups-t1-t2` 的 T1/T2 commits 尚未合併回 `main`）不是本 sprint 的問題，但既然 `docs/TASKS.md` 建議執行順序是「T1/T2 並行 → T3」，建議在排定下一個 sprint（T4）前先把 T1/T2 分支合併回 `main`，避免未來的基準 N 因分支不同而反覆跳動、造成後續 sprint 誤判。

### ✅ What looks good
- 測試對外部邊界的處理正確且比既有 `finnhub_stub`（真 `ThreadingHTTPServer`）更穩定，完全不受 sandbox socket 限制影響（單獨執行 5 個新測試不需 `dangerouslyDisableSandbox`）。
- 斷言風格直接查 SQLite 表內容而非只驗 mock 呼叫次數，且透過 mutation test（拿掉 `mark_notified`）驗證確實偵測到真實回歸，測試品質可信。
- 完全未觸碰 `src/`，符合 spec「不新增/修改應用程式行為」的 non-goal；也未動既有 `tests/test_local_integration.py` 的 `finnhub_stub`，避免波及既有 12 個 socket 測試。
- `CTX`、`_process()` 命名與既有 `tests/test_local_integration.py`、`tests/test_scheduler.py` 慣例一致，降低後續維護者的認知負擔。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻擋性問題；本次驗收無 P0/P1。若要進一步優化，可考慮下一輪把 `chore/db-first-followups-t1-t2` 分支合併回 `main`（見 🟢 建議），避免基準數字在不同分支間漂移。
