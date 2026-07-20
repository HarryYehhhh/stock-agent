# QA Report: local-first-storage-sprint

- **Contract**: docs/contracts/local-first-storage-sprint.md
- **Date**: 2026-07-20
- **Base SHA**: ef7b1296e38480e2e70e8282e35a9217e75de480
- **Branch**: main
- **Cycle**: 2
- **Verdict**: ⚠️ Pass with issues

## Contract checklist
| Deliverable | Status | Note |
|---|---|---|
| 在 `src/agent/db/` 加入 backend selection，local/non-Lambda 預設 local、Lambda 預設 DynamoDB | ✅ | `selected_backend_name()` 覆蓋預設與 override；測試通過。 |
| 加入本地 persistent store | ✅ | 使用 Python stdlib `sqlite3` 單檔資料庫，無新增 dependency。 |
| 保留既有 db module public API | ✅ | `save_message`, `get_history`, `build_messages`, `subscribe`, `unsubscribe`, `get_all_active`, `claim_update_id` 均保留。 |
| `STORAGE_BACKEND=local` + `LOCAL_STORE_PATH` 時只使用該 local store | ✅ | Unit 與 integration 使用 tmp sqlite store 驗證。 |
| `STORAGE_BACKEND=dynamodb` 時保留既有 DynamoDB table config/key/TTL | ✅ | 既有 moto regression 測試通過。 |
| 未設定 `STORAGE_BACKEND` 時依 Lambda env 選擇 backend | ✅ | Unit 覆蓋 non-Lambda local 與 Lambda DynamoDB。 |
| conversation history 最近 `limit` 筆、oldest-first、`build_messages` 只回 `role` + `content` | ✅ | local 與既有 DynamoDB regression 通過。 |
| subscriber active true/false 行為 | ✅ | local 與 DynamoDB regression 通過。 |
| dedup first claim true、duplicate false | ✅ | local 與 DynamoDB regression 通過。 |
| upload command module 可用 `python -m agent.db.upload_local_store` 執行 | ✅ | module entry 可執行；manual invalid backend command 與 pytest 均覆蓋。 |
| upload command dry-run 預設輸出 row counts 且不寫 DynamoDB | ✅ | integration test 通過。 |
| upload command 只有 `--write` 才寫入；缺少/missing local store 清楚失敗 | ✅ | guard tests 通過。 |
| upload command 寫入 DynamoDB 時沿用三張表 schema | ✅ | moto integration 驗證 conversations/subscribers/dedup rows。 |
| `.env.example` 記錄 `STORAGE_BACKEND` / `LOCAL_STORE_PATH` 且不放真實路徑 | ✅ | 使用 `.local/earnings-agent.sqlite3` example path。 |
| repo 內 default local store path 被忽略 | ✅ | `.gitignore` 加入 `.local/`。 |
| 不支援的 `STORAGE_BACKEND` 值明確失敗 | ✅ | `upload_local_store.main()` 會先驗證 `selected_backend_name()`；pytest 與 manual command 均確認 exit 2。 |
| upload write mode 遇到 DynamoDB write error 非 0，local store 不變 | ✅ | 新增測試覆蓋 `put_item` failure、exit 1、local rows unchanged。 |

## Test results
- 測試指令：`make test`
- 結果：pytest 未啟動；command exit 2（底層 `/bin/sh: uv: command not found`，`make: *** [test] Error 127`）
- 失敗測試：無 pytest failure；失敗 phase 為 environment bootstrap
- 既有 suite regression：✅（使用 repo 既有 `.venv/bin/python` 跑同範圍 pytest，55 passed）
- Sprint commit 範圍：`.env.example`, `.gitignore`, `docs/CHANGELOG.md`,
  `docs/specs/local-first-storage.md`,
  `docs/contracts/local-first-storage-sprint.md`,
  `docs/decisions/0001-local-first-storage-backend.md`,
  `docs/qa-reports/local-first-storage-sprint.md`, `src/agent/db/`,
  `tests/conftest.py`, `tests/test_local_integration.py`,
  `tests/test_local_storage.py`, `tests/test_storage_backend.py`,
  `tests/test_upload_local_store.py`

### Unit
| Required | Blocking | Scenario | Command | Environment | Attempt | Duration | Result / Evidence |
|---|---:|---|---|---|---:|---:|---|
| true | true | contract 原始 unit/integration gate | `make test` | shell PATH lacks `uv` | 1 | <0.01s | ❌ exit 2 before pytest; `/bin/sh: uv: command not found` |
| true | true | backend selection, local storage behavior, upload guards, invalid backend guard, write failure/local rows unchanged | `PYTHONPATH=src .venv/bin/python -m pytest tests/test_storage_backend.py tests/test_local_storage.py tests/test_upload_local_store.py -v` | repo `.venv`, Python 3.12.12, pytest 9.0.3, moto/fakes only | 2 | 0.45s | ✅ 14 passed |
| true | true | generator target subset cross-check | `PYTHONPATH=src .venv/bin/python -m pytest tests/test_storage_backend.py tests/test_local_storage.py tests/test_upload_local_store.py tests/test_local_integration.py -v` | repo `.venv`, tmp sqlite, moto/fakes only | 1 | 0.86s | ✅ 16 passed |

### Integration
| Required | Blocking | Scenario | Command | Environment | Attempt | Duration | Result / Evidence |
|---|---:|---|---|---|---:|---:|---|
| true | true | local webhook duplicate update enqueues once; processor subscribe + scheduler reads local subscriber; upload dry-run/write to moto DynamoDB | `PYTHONPATH=src .venv/bin/python -m pytest tests/test_local_integration.py tests/test_upload_local_store.py::test_upload_dry_run_default_does_not_write tests/test_upload_local_store.py::test_upload_write_copies_rows_to_dynamodb -v` | repo `.venv`; pytest tmp sqlite; moto SQS/DynamoDB; Telegram/Gemini/Finnhub stubbed | 1 | 0.85s | ✅ 4 passed |
| true | true | full regression suite | `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v` | repo `.venv`; moto/fakes/stubs; no real AWS/Telegram/Gemini/Finnhub observed | 1 | 1.61s | ✅ 55 passed |
| true | true | Python syntax/import compile check | `PYTHONPATH=src .venv/bin/python -m compileall src tests` | repo `.venv`; no external services | 1 | <0.01s | ✅ passed |

### E2E
| Required | Blocking | Scenario | Command | Environment | Attempt | Duration | Result / Evidence |
|---|---:|---|---|---|---:|---:|---|
| false | false | Not required by contract | Not executed | AGENTS.md has no E2E harness/reset/stop command | 0 | 0s | ✅ Skipped per contract |

### External Smoke
| Required | Blocking | Scenario | Command | Environment | Attempt | Duration | Result / Evidence |
|---|---:|---|---|---|---:|---:|---|
| false | false | Not requested / non-blocking | Not executed | Contract forbids real AWS/Telegram/Gemini/Finnhub for this sprint | 0 | 0s | ✅ Skipped per contract |

### Environment failure diagnostics
- **Surface Error**: `make test` exits 2 before pytest starts.
- **Root Cause**: `uv` is not available in the current shell PATH.
- **Cause Chain**: `make test` -> `PYTHONPATH=src uv run pytest tests/ -v` -> `/bin/sh` cannot resolve `uv` -> make reports target failure.
- **Diagnostics**: `which uv` returned exit 1; `.venv/bin/python -m pytest --version` returned pytest 9.0.3; `.venv/bin/python` exists and points to an existing Python 3.12 runtime.
- **Remediation Applied**: no dependency install or upgrade; used repo existing `.venv/bin/python` to run the same pytest scopes. Original `make test` remains blocked by environment PATH, not by a product/test failure.

## Findings

### 🔴 Critical（Must Fix）
- 無問題。

### 🟡 Warnings（Should Fix）
- **AGENTS.md:9**: Required verification command `make test` is currently not runnable in this shell because `uv` is missing from PATH. Impact: a reviewer or local gate following AGENTS exactly cannot reproduce the blocking command, even though the same pytest scope passes through the existing repo `.venv` with 55 passed.
  建議修法：在 runner/developer bootstrap 中確保 `uv` 在 PATH，或把 project test command 改成可由 repo-contained runtime 穩定執行的形式。

### 🟢 Suggestions
- 無問題。

### ✅ What looks good
- 首輪 code finding 已關閉：`upload_local_store.main()` 現在會驗證 unsupported `STORAGE_BACKEND` 並 exit 2。
- 首輪 test finding 已關閉：新增 DynamoDB `put_item` failure test，確認 write mode exit 1 且 local sqlite rows unchanged。
- Backend boundary 仍集中在 `src/agent/db/`；handlers 維持呼叫既有 db public functions。
- Design Pattern Fit 仍符合 spec/contract：最小 Strategy-like selector，無 ORM/service layer，無新增 dependency。
- Integration tests 使用 tmp local store + moto/fakes/stubs，未呼叫真 AWS、Telegram、Gemini 或 Finnhub。

## 結論
- 是否可合併：是；目前僅剩 `uv` PATH 的環境 warning，無 P0/P1 產品或測試缺陷。
- 最該先處理的 1–3 件事：
  1. 修復 `uv` PATH / bootstrap，讓 `make test` 原始 gate 可重現。
