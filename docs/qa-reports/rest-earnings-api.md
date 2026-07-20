# QA Report: rest-earnings-api

- **Contract**: 無正式 `docs/contracts/rest-earnings-api.md`；本次以 main Codex 傳入驗收範圍為準。
- **Date**: 2026-07-21
- **Base SHA**: `5c5f3f279377cee23a429c3c51e61f65ce8ab991`
- **Branch**: `codex/local-first-storage`
- **Verdict**: ✅ Pass

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| local FastAPI 新增 `GET /earnings?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&symbol=AAPL` | ✅ | `src/agent/local_server.py:47` 定義 endpoint，`date_from` / `date_to` 為必填 date query。 |
| Endpoint 呼叫既有 `get_earnings_calendar`，不新增 AWS Lambda/API Gateway production route | ✅ | `src/agent/local_server.py:60` 呼叫既有工具；`rg` 僅在 local server 找到 `/earnings` route，`infra/template.yaml` 無新增路由。 |
| Finnhub 呼叫維持 RESTful HTTP API 形式 | ✅ | `src/agent/tools/earnings.py:35` 使用 real `httpx.Client.get("/calendar/earnings", params=...)`；integration 測試以 in-process HTTP stub 驗證 method/path/query。 |
| 成功時回傳 `{"earningsCalendar": [...]}` JSON envelope | ✅ | `src/agent/local_server.py:70` 回傳 envelope；`tests/test_local_integration.py:272` 驗證 response JSON。 |
| 成功時寫入 `LOCAL_STORE_PATH` 的 SQLite `earnings_calendar` table | ✅ | `src/agent/tools/earnings.py:42` 僅 local backend 寫入；`tests/test_local_integration.py:283` 查實際 SQLite row。 |
| Duplicate/upsert 行為正確 | ✅ | `tests/test_local_integration.py:261` 重複呼叫 endpoint 後 row 仍單筆；`tests/test_storage_backend.py:48` 驗證同 key 更新欄位。 |
| Invalid date range / format 回 422 | ✅ | `src/agent/local_server.py:53` 驗證 range；FastAPI date parser 處理 format；integration tests 皆通過。 |
| Finnhub 401 / 429 / 500 回 502、去敏、不寫入 SQLite | ✅ | `src/agent/local_server.py:67` 統一 upstream HTTP error；parametrize integration tests 覆蓋 401/429/500。 |
| Malformed JSON 回 502、去敏、不寫入 SQLite | ✅ | `src/agent/local_server.py:67` 捕捉 `JSONDecodeError`；integration test 覆蓋 malformed response。 |
| Timeout 回 504、去敏、不寫入 SQLite | ✅ | `src/agent/local_server.py:65` 捕捉 `httpx.TimeoutException`；integration test 覆蓋 delayed stub + short timeout。 |
| README 提供 local curl 範例 | ✅ | `README.md:13` 新增 `/earnings` curl 範例。 |
| Telegram/webhook 行為無 regression | ✅ | Full suite 72/72 passed，包含 webhook、processor、scheduler、agent tests。 |

## Test results

- Unit Required：✅ true
- Integration Required：✅ true
- Integration 方式：✅ FastAPI `TestClient` → `get_earnings_calendar` → real `httpx.Client` → in-process `ThreadingHTTPServer` Finnhub stub → tmp `LOCAL_STORE_PATH` SQLite；不是只 mock internal client。
- 既有 suite regression：✅ 無 regression
- 失敗測試：無

| Gate | Command | Environment | Scenario | Attempt | Duration | Result / Evidence |
|---|---|---|---|---:|---:|---|
| Full suite | `make test` | sandbox | 專案完整測試 | 1 | <1s | ❌ exit 2；`uv` 無法初始化 `/Users/yehhungwei/.cache/uv`，`Operation not permitted`。判定為 sandbox cache 權限問題。 |
| Full suite | `make test` | escalated for uv cache | 專案完整測試 | 2 | 7.82s | ✅ 72 passed / 0 failed / 0 skipped |
| Integration | `PYTHONPATH=src uv run pytest tests/test_local_integration.py -v` | sandbox | local SQLite + Finnhub HTTP stub | 1 | <1s | ❌ exit 2；同樣因 `/Users/yehhungwei/.cache/uv` sandbox 權限失敗。 |
| Integration | `PYTHONPATH=src uv run pytest tests/test_local_integration.py -v` | escalated for uv cache | local SQLite + Finnhub HTTP stub | 2 | 6.79s | ✅ 16 passed / 0 failed / 0 skipped |

### Integration scenario evidence

- REST success + JSON envelope + SQLite actual row/upsert：`tests/test_local_integration.py::test_rest_earnings_returns_finnhub_envelope_and_writes_sqlite`
- 422 invalid range：`tests/test_local_integration.py::test_rest_earnings_rejects_invalid_date_range`
- 422 invalid format：`tests/test_local_integration.py::test_rest_earnings_rejects_invalid_date_format`
- Upstream 401/429/500 → 502 + token not leaked + no SQLite write：`tests/test_local_integration.py::test_rest_earnings_upstream_http_errors_do_not_leak_token_or_write_sqlite`
- Malformed JSON → 502 + token not leaked + no SQLite write：`tests/test_local_integration.py::test_rest_earnings_malformed_response_returns_502_and_does_not_write_sqlite`
- Timeout → 504 + token not leaked + no SQLite write：`tests/test_local_integration.py::test_rest_earnings_timeout_returns_504_and_does_not_write_sqlite`
- Stub cleanup：`finnhub_stub` fixture 使用 `finally: server.shutdown(); server.server_close()`；task-owned `httpx.Client` 在各 test `finally` close。

### 待 commit 檔案

- `AGENTS.md`
- `CLAUDE.md`
- `README.md`
- `src/agent/db/backend.py`
- `src/agent/local_server.py`
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
- `docs/qa-reports/store-finnhub-earnings-sqlite.md`
- `docs/qa-reports/rest-earnings-api.md`
- `docs/specs/README.md`
- `docs/tech-stack.md`
- `src/agent/db/watchlist.py`
- `tests/test_watchlist.py`

## Findings

### 🔴 Critical（Must Fix）

- 無問題。

### 🟡 Warnings（Should Fix）

- 無問題。

### 🟢 Suggestions

- 無問題。

### ✅ What looks good

- Endpoint 實作範圍精準，只改 local FastAPI shim，未擴張到 AWS production route。
- Error handling 對使用者回覆固定 upstream 訊息，沒有把 Finnhub token 或原始 upstream URL 暴露到 HTTP response。
- Integration 測試跨過 FastAPI、real HTTP client、HTTP stub、SQLite 檔案邊界，能驗證主要整合鏈，而非只 mock 函式。
- SQLite upsert 用 `(symbol, earning_date)` primary key，重複同步不會產生重複資料列。

## 結論

- 是否可合併：是
- 最該先處理的 1–3 件事：無 blocking 項目；若要後續強化，可另外規劃是否儲存 Finnhub 原始 JSON payload，但這不在本次明確 scope。
