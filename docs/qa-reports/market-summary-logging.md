# QA Report: market-summary-logging

- **Contract**: 無 contract（需求已釐清後直接對照「原始需求 + 程式碼品質」驗收）
- **Date**: 2026-07-25
- **Verdict**: ✅ Pass

## 需求 checklist
| 項目 | Status | Note |
|---|---|---|
| `market_summary.py` 改用 `aws_lambda_powertools.Logger`（與 `scheduler_handler.py` 一致） | ✅ | `log = Logger()`，移除 `logging.getLogger(__name__)` |
| 移除 CLI-only 的 `basicConfig`（不再需要，Logger 預設即輸出） | ✅ | `if __name__ == "__main__":` 只剩 `push_daily_summary()` |
| `refresh_news` 補同步筆數 log | ✅ | `log.info("refresh_news: inserted %d new rows (days_back=%d)", inserted, days_back)`（market_summary.py:148） |
| `push_daily_summary` 補推播訂閱者數 log | ✅ | `log.info("push_daily_summary: pushed to %d/%d active subscribers", sent, len(subscribers))`（market_summary.py:304） |
| `sync_earnings` 補同步筆數 log | ✅ | `logger.info("sync_earnings: synced %d/%d symbols (failed=%s)", ...)`（scheduler_handler.py:70-73） |
| skip 原因的既有 log（no news / no active subscribers）維持可見 | ✅ | 原本就有 `log.info("no news to summarize, skip push")` 等，未被動到 |
| CLI 模式仍可輸出 | ✅ | 實測 `PYTHONPATH=src uv run python -m agent.market_summary`-style 呼叫路徑不變，powertools Logger 預設即寫 stdout（見下方 Reasoning） |
| 驗收情境：notify 模式有 news + active 訂閱者時 log 出現「推播了 N 位訂閱者」 | ✅ | 新測試 `test_push_daily_summary_logs_pushed_count`，已用 caplog 驗證且用暫時破壞訊息文字的方式確認測試真的會 fail-if-broken（見下方 Test results） |
| 現有測試全綠、無 regression | ✅ | 235 passed（沙箱環境下有 12 個既有整合測試因 socket bind 權限失敗，屬沙箱限制非本次改動所致；停用沙箱後 235/235 全綠） |

## Test results
- 測試指令：`cd /Users/yehhungwei/stock-agent-worktrees/market-summary-logging && PYTHONPATH=src uv run pytest tests/ -v`
- 結果（停用沙箱，真實結果）：**235 passed**（原 234 + 新增 1 條 `test_push_daily_summary_logs_pushed_count`）
- 失敗測試：無
- 既有 suite regression：✅ 無
- 額外驗證：在沙箱模式下跑同一指令會出現 12 個 `test_local_integration.py` 的 `PermissionError: socket.bind`（`ThreadingHTTPServer` 在沙箱內無法 bind port），這是本機沙箱環境限制，與本次改動、與 CI/正常本機環境無關；停用沙箱後即全部通過，可排除是本次改動造成的 regression。
- 新測試有效性驗證：暫時把 `push_daily_summary` 結尾的 log 文字從 `"push_daily_summary: pushed to %d/%d active subscribers"` 改成 `"push_daily_summary: sent to %d of %d subs"` 後重跑該測試 → **FAILED**（assert False，`caplog.records` 裡確實抓不到舊字串）；還原後重跑全 235 條 → 全綠。證明該測試不是空判斷，會真的抓到 log 訊息被改壞的情況。

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- **market_summary.py:283, 288**（既有程式碼，非本次改動）：`log.info("no news to summarize, skip push")` / `log.info("no active subscribers, skip push")` 沒有帶任何變數（例如 news/subscriber 相關的上下文），排查時只知道「skip 了」但看不出是哪個日期/哪次 sync 觸發的。目前每次 Lambda invocation 都會有獨立的 CloudWatch log stream 與 `push_daily_summary` 結尾的統計 log 互相印證，影響有限，可視情況日後再加。這不在本次需求範圍內（需求只要求關鍵節點補 log，不含既有 skip log 的內容擴充），故不列為 Warning。

### ✅ What looks好
- **Surgical**：`market_summary.py` diff 只有 4 處改動（import、`log = Logger()`、`refresh_news` 補一行、`push_daily_summary` 補計數器+一行、移除 `basicConfig`），`scheduler_handler.py` 只加了 3 行（`symbols` 抽變數 + 一行 log），沒有動到任何無關邏輯、註解或格式。
- **語法相容性**：實測確認 powertools `Logger.info/warning/exception` 完全相容既有的 `%s`/`%d` 舊式 lazy formatting 呼叫方式（例如 `log.warning("company news fetch failed for %s: %s", symbol, e)` 這種既有呼叫未被改動也仍正常運作），generator 沒有畫蛇添足去改寫成 f-string 或 `extra=` 結構化欄位。
- **核心 observability 缺口確實被修好**：實測時看到 `Captured stdout call` 印出 JSON 格式的 log 行，例如：
  `{"level":"INFO","location":"push_daily_summary:304","message":"push_daily_summary: pushed to 2/2 active subscribers", ...}`
  這證明 powertools `Logger()` 預設 handler 綁定 stdout，且在 import 當下就完成 handler 設定（不像 stdlib `logging.getLogger(__name__)` 需要外部 `basicConfig` 才有 handler）。Lambda runtime 會把 stdout 轉送到 CloudWatch，因此這個修法確實解決了「Lambda 內 `push_daily_summary`/`refresh_news` 的 log 完全不輸出到 CloudWatch」的核心問題。`scheduler_handler.py` 本來就用同一套 Logger 且已在生產環境驗證過（`@logger.inject_lambda_context` 裝饰的 `handler`），這次讓 `market_summary.py` 統一用同一套機制，一致性合理。
- **CLI 模式未壞**：移除 `logging.basicConfig(level=logging.INFO)` 後，`if __name__ == "__main__": push_daily_summary()` 仍會輸出（因為 Logger() 本身自帶 handler），且輸出格式從 plain text 變成 JSON structured log——這是 trade-off 但可接受，不影響功能，也非本次需求要求維持格式，只要求「CLI 模式仍可輸出」。
- **測試設計**：新增測試命名清楚、docstring 說明了為何用 `caplog` 而非 `capsys`（powertools Logger 的 stream handler 在匯入當下就綁死原始 stdout，早於 `capsys` patch），這個技術判斷正確且對後續維護者有幫助。測試複用既有 `push_mocks` fixture（2 個 active subscriber + mock news/groq/send），沒有重複建置測試環境。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無（本次無 Critical/Warning，🟢 Suggestion 為選配，不影響合併）
