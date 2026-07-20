# QA Report: store-finnhub-earnings-sqlite

- **Contract**: 無獨立 contract；驗收依據為使用者需求「將 Finnhub API 回應存到 SQLite，table columns 有 symbol, earning_date, extended_hour, eps_estimate, revenue_estimate」
- **Date**: 2026-07-20
- **Base SHA**: 5c5f3f279377cee23a429c3c51e61f65ce8ab991
- **Branch**: codex/local-first-storage
- **Verdict**: ✅ Pass

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| SQLite table stores Finnhub earnings calendar data | ✅ | `earnings_calendar` table exists in local SQLite schema. |
| Required columns: `symbol`, `earning_date`, `extended_hour`, `eps_estimate`, `revenue_estimate` | ✅ | Columns map from Finnhub `symbol`, `date`, `hour`, `epsEstimate`, `revenueEstimate`. |
| Successful Finnhub calendar fetch writes rows to SQLite | ✅ | Local backend calls `upsert_earnings_calendar()` after successful HTTP status and JSON parse. |
| Duplicate same symbol/date does not create duplicate rows | ✅ | `PRIMARY KEY(symbol, earning_date)` with upsert update semantics. |
| Error responses do not write partial/invalid data | ✅ | Covered for HTTP 401/429/500, malformed JSON, and timeout. |
| Integration Required 判定 | ✅ | Required；跨 SQLite filesystem boundary 與 Finnhub HTTP client boundary。 |
| Integration transport is real HTTP boundary | ✅ | Test uses `httpx.Client` against in-process `ThreadingHTTPServer`; not an internal client mock. |
| AGENTS.md minimal/design-pattern boundary | ✅ | No new pattern-first abstraction for this feature; implementation stays in existing tool/backend boundary. |

## Test results

### Unit / full gate

- **Unit/Integration required**: Unit/full suite required by AGENTS.md.
- **測試指令**: `make test`
- **Environment**: macOS Darwin, Python 3.12.12, pytest-9.0.3, uv virtualenv `.venv`; no real Finnhub/AWS calls.
- **Scenario**: Full repo regression suite, including storage backend, tools, local integration, scheduler, webhook, upload, watchlist.
- **Attempt 1**:
  - **Duration**: 未進入 pytest collection。
  - **Result**: exit code 2
  - **Surface Error**: `Failed to initialize cache at /Users/yehhungwei/.cache/uv`
  - **Root Cause**: sandbox 無權限讀寫 uv cache。
  - **Cause Chain**: `make test` → `uv run pytest tests/ -v` → uv 初始化 user cache → sandbox denied `/Users/yehhungwei/.cache/uv/sdists-v9/.git`。
  - **Diagnostics**: `Operation not permitted (os error 1)`；未輸出 credential。
  - **Remediation/result**: 以精確 escalation 重跑同一指令，允許 `make test` 存取 uv cache。
  - **精確權限 blocker**: uv cache path `/Users/yehhungwei/.cache/uv` 在 workspace-write sandbox 外。
- **Attempt 2**:
  - **Duration**: 4.36s
  - **Result**: ✅ 64 passed / 0 failed / 0 skipped
  - **Evidence**: `tests/test_storage_backend.py::test_local_backend_upserts_earnings_calendar PASSED`; `tests/test_local_integration.py::test_earnings_calendar_fetch_writes_sqlite_with_local_finnhub_stub PASSED`; full output reports `64 passed in 4.36s`。
- **既有 suite regression**: ✅

### Integration gate

- **Unit/Integration required**: Integration required；feature crosses local SQLite/filesystem and Finnhub HTTP client boundary.
- **測試指令**: `PYTHONPATH=src uv run pytest tests/test_local_integration.py -v`
- **Environment**: tmp_path SQLite DB, fake Finnhub API key, in-process `ThreadingHTTPServer` on `127.0.0.1`, real `httpx.Client`, no real external Finnhub calls.
- **Scenario**:
  - successful `/calendar/earnings` method/path/query and SQLite write;
  - duplicate sync/upsert behavior;
  - HTTP 401/429/500 do not write;
  - malformed JSON does not write;
  - timeout does not write;
  - existing local webhook/scheduler integration remains passing.
- **Attempt 1**:
  - **Duration**: 未進入 pytest collection。
  - **Result**: exit code 2
  - **Surface Error**: `Failed to initialize cache at /Users/yehhungwei/.cache/uv`
  - **Root Cause**: sandbox 無權限讀寫 uv cache。
  - **Cause Chain**: `uv run pytest` → uv 初始化 user cache → sandbox denied `/Users/yehhungwei/.cache/uv/sdists-v9/.git`。
  - **Diagnostics**: `Operation not permitted (os error 1)`；未輸出 credential。
  - **Remediation/result**: 以精確 escalation 重跑同一指令。
  - **精確權限 blocker**: uv cache path `/Users/yehhungwei/.cache/uv` 在 workspace-write sandbox 外。
- **Attempt 2**:
  - **Duration**: 3.51s
  - **Result**: ✅ 8 passed / 0 failed / 0 skipped
  - **Evidence**: output reports all 8 tests in `tests/test_local_integration.py` passed, including success write, 401/429/500, malformed response, and timeout cases.
- **既有 suite regression**: ✅

### 待 commit 檔案

- `AGENTS.md`
- `CLAUDE.md`
- `src/agent/db/backend.py`
- `src/agent/scheduler_handler.py`
- `src/agent/tools/earnings.py`
- `tests/test_local_integration.py`
- `tests/test_scheduler.py`
- `tests/test_storage_backend.py`
- `tests/test_tools.py`
- `.github/workflows/auto-merge.yml`
- `architecture.png`
- `docs/contracts/README.md`
- `docs/decisions/README.md`
- `docs/qa-reports/README.md`
- `docs/qa-reports/fixed-earnings-watchlist.md`
- `docs/specs/README.md`
- `docs/tech-stack.md`
- `src/agent/db/watchlist.py`
- `tests/test_watchlist.py`
- `docs/qa-reports/store-finnhub-earnings-sqlite.md`

## Findings

### 🔴 Critical（Must Fix）

- 無問題。

### 🟡 Warnings（Should Fix）

- 無問題。

### 🟢 Suggestions

- 無問題。

### ✅ What looks good

- `src/agent/db/backend.py:90` 建立的 `earnings_calendar` schema 符合指定欄位，並以 `(symbol, earning_date)` primary key 支援重複同步 upsert。
- `src/agent/db/backend.py:205` 將 Finnhub camelCase 欄位映射到 SQLite snake_case 欄位，且略過缺少 `symbol` 或 `date` 的不可辨識 row。
- `src/agent/tools/earnings.py:35` 先完成 HTTP status 檢查、JSON parse、既有 symbol filter 與 100 筆裁切後才在 local backend 寫入，錯誤路徑不會產生 partial write。
- `tests/test_local_integration.py:100` 使用 in-process HTTP server 與 real `httpx.Client`，有實際跨 HTTP transport boundary，不只是 mock tool function。
- `tests/test_local_integration.py:165` 覆蓋 401/429/500；`tests/test_local_integration.py:186` 覆蓋 malformed JSON；`tests/test_local_integration.py:205` 覆蓋 timeout。

## 結論

- **是否可合併**: 是
- **最該先處理的 1–3 件事**:
  1. 無 P0/P1 阻擋項。
  2. 合併前由 main Codex 彙整 `docs/CHANGELOG.md`；本 evaluator 未修改 changelog。
