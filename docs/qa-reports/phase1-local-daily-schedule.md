# QA Report: phase1-local-daily-schedule

- **Contract**: 無正式 contract（docs/TASKS.md「Phase 1 — 本地每日自動運轉」，直接對照需求驗收）
- **Date**: 2026-07-23
- **Verdict**: ✅ Pass

## 需求 checklist

| Deliverable | Status | Note |
|---|---|---|
| 每日摘要 CLI `python -m agent.market_summary`（refresh_news → generate_market_summary → 推播 active 訂閱者） | ✅ | `push_daily_summary()`（`src/agent/market_summary.py:263-284`）+ `__main__`；流程與 `local_server.py:52-55` 現有 `/market-summary` endpoint 的 `refresh_news → generate_market_summary` 組合一致 |
| 無內容不發空訊息 | ✅ | `result["news"]["big7"]`/`["ai"]` 皆空時直接 return，不呼叫 `get_all_active`/`send_message`；`test_push_daily_summary_skips_send_when_no_news` 驗證 |
| 單一訂閱者失敗不中斷 | ✅ | `for chat_id in subscribers: try: send_message(...) except Exception: log.exception(...)`，無 `raise`/`break`；`test_push_daily_summary_send_failure_does_not_stop_other_subscribers` 驗證（call_count 仍為 2） |
| Makefile `daily-summary` target + `.PHONY` | ✅ | `Makefile:22-23`；`.PHONY` 行已補上 `daily-summary`；`make -n daily-summary` 輸出正確指令 |
| launchd plist（`scripts/`），台北 08:00 跑三個 make target，處理 WorkingDirectory 與 uv PATH，log 導檔 | ✅ | `scripts/com.earnings-agent.daily.plist`：`WorkingDirectory=/Users/yehhungwei/stock-agent`、`PATH` 含 `/Users/yehhungwei/.local/bin`（本機 `which uv` 實測相符）、`StartCalendarInterval Hour=8/Minute=0`、`StandardOutPath`/`StandardErrorPath` 皆導向 `.local/logs/daily.log`、`RunAtLoad=false` |
| 安裝文件 `docs/local-daily-schedule.md`（load/unload、改時間、看 log、手動觸發） | ✅ | 涵蓋全部子項，`launchctl load` 明確標註「需使用者本人執行」 |
| 步驟與實際檔名一致 | ✅ | plist 檔名 `com.earnings-agent.daily.plist`、`Label`/`launchctl start` 皆用 `com.earnings-agent.daily`，文件與檔案逐字一致 |
| CLI 單元測試覆蓋正常推播/無內容/單一失敗不中斷 | ✅ | `tests/test_market_summary.py` 新增 4 案例，另加碼「無訂閱者」情境 |
| `make test` 全綠 | ✅ | 見下方 Test results |
| `plutil -lint` 通過 | ✅ | `plutil -lint scripts/com.earnings-agent.daily.plist` → OK |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -q`
- 沙盒內結果：127 passed, 12 errors（皆為 `tests/test_local_integration.py` 既有 socket bind 沙盒限制，與本次改動無關）
- 非沙盒重跑：**139 passed, 0 failed**
- `PYTHONPATH=src uv run pytest tests/test_market_summary.py -v`：12 passed（含新增 4 個 `push_daily_summary` 案例）
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- **`&&` 串接會讓 sync 失敗擋住 notify/daily-summary**（`scripts/com.earnings-agent.daily.plist:23`）：已在需求 TASKS.md Phase 1 第 3 點明確定案為「失敗留 log、不做告警系統」，且文件「已知限制」一節有明確揭露此行為。因此判定**非 Critical**，屬於已知且被需求接受的設計取捨，僅列記錄供 Phase 2 遷移雲端時參考（是否要拆成獨立排程各自失敗互不影響）。
- `push_daily_summary()` 無論是否有新聞都會呼叫 `generate_market_summary()`，其內部 `fetch_index_quotes()` 每次固定打 4 次 Finnhub（大盤 ETF 報價），即使最終判定 no-news 要 skip push 也已經打完。此為 `generate_market_summary` 既有行為（非本次 diff 新增），不在本次改動範圍內，僅記錄不要求修改。
- plist 內路徑（`WorkingDirectory`、log 路徑、`PATH` 裡的 `uv` 路徑）皆為寫死的本機絕對路徑，文件「已知限制」一節已如實揭露，換機器需手動改，符合「Phase 1 僅本機」的階段性目標，可接受。

### ✅ What looks good
- `push_daily_summary()` 的訂閱者來源（`get_all_active()`）與 per-subscriber try/except-continue 錯誤處理，完全比照 `scheduler_handler.py` 的 `_notify_live()` 既有模式（`src/agent/scheduler_handler.py:185-189`），沒有另外發明新模式。
- `__main__` 區塊只做 `logging.basicConfig` + 呼叫入口函式，簡潔對齊本檔案既有的 plain `logging` 用法（非強行套用 `scheduler_handler.py` 的 `aws_lambda_powertools.Logger`），沒有過度設計；也不需要像 `scheduler_handler` 一樣做 argv 分支（本 CLI 只有單一行為），符合 Simplicity First。
- 新測試用既有 `local_store`/`mock_finnhub`/`mock_groq`/`mock_quotes` fixtures，`local_store` 每個測試用獨立 `tmp_path` SQLite，無跨測試污染；`get_all_active()` 的 local backend 實作用 `ORDER BY chat_id`（`src/agent/db/backend.py:337`），多訂閱者測試的順序斷言具確定性、不會 flaky。
- 新增測試不只覆蓋 3 個關鍵行為，還多補了「無訂閱者時不發送且不呼叫 Groq」的情境，測試命名與斷言清楚對應行為本身而非實作細節。
- plist 內附有清楚的 XML 註解說明用途與改時間的方式，`docs/local-daily-schedule.md` 額外提醒「launchd 不會自動建立 log 目錄」這個容易踩雷的細節，屬於高品質的營運文件。
- Makefile `daily-summary` target 與既有 `notify-earnings`/`sync-earnings` 風格完全一致（`PYTHONPATH=src uv run python -m agent.xxx`）。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞項；後續（非本次必須）：Phase 2 雲端遷移時重新評估是否要讓 sync/notify/daily-summary 互不阻塞。
