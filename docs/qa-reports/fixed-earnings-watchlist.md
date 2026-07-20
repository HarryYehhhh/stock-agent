# QA Report: fixed-earnings-watchlist

- **Contract**: 無獨立 contract；驗收依據為使用者需求與 main Codex 傳入標準
- **Date**: 2026-07-20
- **Base SHA**: 5c5f3f279377cee23a429c3c51e61f65ce8ab991
- **Branch**: codex/local-first-storage
- **Verdict**: ✅ Pass

## Contract checklist
| Deliverable | Status | Note |
|---|---|---|
| SQLite watchlist 保存固定 28 筆 exchange+ticker | ✅ | `watchlist` table 含 `exchange`, `ticker`, `position`；初始化會 upsert defaults 並刪除非預設 ticker。 |
| watchlist exact sync 固定 28 筆 | ✅ | `tests/test_watchlist.py` 驗證 SQLite 內容與 28 筆指定清單完全一致。 |
| Lambda/dynamodb 預設仍使用固定 28 筆，不依賴不可持久 SQLite | ✅ | `agent.db.watchlist.get_watchlist()` 在非 local backend 回傳 code defaults。 |
| scheduler 逐 ticker 查明日財報，Finnhub symbol 只用 ticker | ✅ | scheduler 呼叫 `get_earnings_calendar(tomorrow, tomorrow, symbol=ticker)`；unit gate 驗證 outbound symbol。 |
| 不再使用 tech industry filter | ✅ | scheduler 不再匯入或呼叫 `get_company_industry` / `_filter_tech`。 |
| ETF 仍保存，無財報不通知 | ✅ | watchlist 包含 QQQ、SOXX；scheduler 只把查詢回傳 rows 的 item 放進 prompt。 |
| 當日有財報者全部列入 prompt，不再挑 5-10 家 | ✅ | prompt 明確要求不要自行挑選或省略；unit gate 驗證 prompt 不含 5-10。 |
| 所有 active subscribers 使用同一通知 | ✅ | scheduler 先產生一次 `summary` / `body`，再對 active subscribers 逐一送出。 |
| 外部 API 不真打 | ✅ | unit/integration 皆使用 monkeypatch / fakes；完整 suite 的工具層 HTTP 也由 mock 覆蓋。 |

## Test results
- Unit required: true
- Unit command: `PYTHONPATH=src uv run pytest tests/test_scheduler.py tests/test_watchlist.py -v`
- Unit environment: macOS/darwin, Python 3.12.12, pytest 9.0.3, project `.venv`
- Unit scenario: scheduler fixed watchlist 行為、SQLite seed exact 28、dynamodb fallback defaults
- Unit attempt: 第 1 次 sandbox 失敗，exit 2，phase=environment bootstrap，root cause=`uv` 無法開啟 `/Users/yehhungwei/.cache/uv/sdists-v9/.git`；提升權限後原命令 retry 1 次
- Unit duration: 0.31s
- Unit result/evidence: 7 passed / 0 failed / 0 skipped
- Integration required: true
- Integration command: `PYTHONPATH=src uv run pytest tests/test_local_integration.py -v`
- Integration environment: tmp SQLite via `LOCAL_STORE_PATH`, fake Finnhub/Gemini/Telegram, moto AWS/SQS/DynamoDB fixtures
- Integration scenario: local webhook dedup；processor `/subscribe` 寫入 tmp SQLite 後 scheduler 共用 local store，並以 fake `get_earnings_calendar(..., symbol=...)` 驗證 outbound application contract
- Integration attempt: 第 1 次以提升權限執行，避免已知 uv cache sandbox 問題
- Integration duration: 0.37s
- Integration result/evidence: 2 passed / 0 failed / 0 skipped
- Regression command: `make test`
- Regression attempt: 第 1 次 sandbox 失敗，exit 2，phase=environment bootstrap，root cause 同 uv cache 權限；提升權限後原命令 retry 1 次
- Regression duration: 1.22s
- Regression result/evidence: 57 passed / 0 failed / 0 skipped
- HTTP transport contract: 本次未修改 Finnhub HTTP transport；既有 `tests/test_tools.py::test_earnings_passes_symbol_to_finnhub` 覆蓋 `symbol` 參數轉大寫送出，`test_earnings_filters_client_side_when_finnhub_returns_all` 覆蓋 Finnhub 忽略 symbol 時的 client-side filter，足以覆蓋本 feature 依賴的工具邊界。
- 既有 suite regression: ✅
- 待 commit 檔案:
  - `CLAUDE.md`
  - `.github/workflows/auto-merge.yml`
  - `architecture.png`
  - `docs/contracts/README.md`
  - `docs/decisions/README.md`
  - `docs/qa-reports/README.md`
  - `docs/qa-reports/fixed-earnings-watchlist.md`
  - `docs/specs/README.md`
  - `docs/tech-stack.md`
  - `src/agent/db/backend.py`
  - `src/agent/db/watchlist.py`
  - `src/agent/scheduler_handler.py`
  - `tests/test_local_integration.py`
  - `tests/test_scheduler.py`
  - `tests/test_watchlist.py`

## Findings

### 🔴 Critical（Must Fix）
- 無問題。

### 🟡 Warnings（Should Fix）
- 無問題。

### 🟢 Suggestions
- 無。

### ✅ What looks good
- watchlist defaults 與 SQLite seed 邏輯維持單一來源，local 與 Lambda/dynamodb 路徑都能取得固定 28 筆。
- scheduler 移除產業 filter 後，查詢邊界清楚：逐 ticker 查詢、單一 ticker 失敗不阻斷其他 ticker。
- 測試覆蓋 fixed list exact sync、dynamodb fallback、ETF 無財報不進 prompt、所有 subscribers 共用同一 summary 的主要路徑。

## 結論
- 是否可合併：是
- 最該先處理的 1-3 件事：無 P0/P1；合併前只需確認 unrelated dirty files 是否屬於同一 PR 範圍。
