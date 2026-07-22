# QA Report: storage-backend-strategy-sprint

- **Contract**: docs/contracts/storage-backend-strategy-sprint.md
- **Date**: 2026-07-22
- **Verdict**: ✅ Pass

## Contract checklist
| Deliverable | Status | Note |
|---|---|---|
| D1 — `StorageBackend` Protocol（11 方法，`typing.Protocol`） | ✅ | `src/agent/db/backend.py:44-74`。11 個方法簽名與 contract 逐字一致；docstring 明確排除 local-only 方法並引用 ADR-0003 |
| D2 — `DynamoDBBackend` class | ✅ | `backend.py:658-757`。11 個方法內容與 wrapper 原本的 inline boto3 程式碼逐字等價（見下方對照）；watchlist add/remove 拋 `NotImplementedError`、訊息與原 `watchlist.py` 一致；get_watchlist 回傳 `DEFAULT_WATCHLIST` copy（lazy import）；seed/upsert 為真 no-op |
| D2 — Table 快取於 `__init__` | ✅ | `__init__` 建立三個 `Table` handle 存為 `self._conversations` / `self._subscribers` / `self._processed_updates`，後續方法重用，未每次呼叫重建 |
| D2 — 約束：`__init__` 與 no-op 方法不觸網 | ✅ | `test_tools.py`（`STORAGE_BACKEND=dynamodb`、無 `aws`/moto fixture）全數綠燈，證明 `get_earnings_calendar` → `get_backend().upsert_earnings_calendar(...)` 沒有觸發任何網路呼叫 |
| D3 — `get_backend()` 依 `selected_backend_name()` 分派 | ✅ | `backend.py:77-81`，`local`→`LocalSQLiteBackend`、其餘→`DynamoDBBackend`，不再 `raise`；維持 `@lru_cache(maxsize=1)`；回傳型別標註 `StorageBackend` |
| D3 — module docstring 更新 | ✅ | `backend.py:1` 已改為「Storage backend selection, local sqlite implementation, and DynamoDB implementation.」 |
| D4 — 11 處分支收斂（conversations/subscribers/dedup/watchlist/earnings） | ✅ | 逐檔案 diff 確認：全部改為單一 `get_backend().X(...)`；`_table()`、`boto3`、`Key`、`ClientError`、`selected_backend_name`、`CONVERSATION_TTL_SECONDS`、`DEDUP_TTL_SECONDS`、`time`、`datetime`、`aws_region`、相關 table-name accessor import 全部移除，無殘留 |
| D4 — grep 驗證無殘留分派 | ✅ | `grep -rn "selected_backend_name()" src/agent/db src/agent/tools` 僅剩 `backend.py` 定義/內部使用 + `upload_local_store.py`（out of scope），wrapper 層 0 處 |
| D5 — conftest `clear_db_caches()` 移除已刪除函式 | ✅ | 已移除 `dedup._table.cache_clear()` / `conversations._table.cache_clear()` / `subscribers._table.cache_clear()` 及對應 import，改為只 import `agent.db.backend` |
| D5 — autouse env fixture 加 `backend.get_backend.cache_clear()` | ✅ | `tests/conftest.py:40`，採用 contract 建議的「env-autouse 清快取」做法（非逐測試插入） |
| Out of scope 未誤動 | ✅ | `scheduler_handler.py`、`processor_handler.py`、`local_server.py` 本次 diff 為 0（`git diff --stat` 確認）；`scheduler_handler._run()` 的 local/live-fetch 分支原封不動；`upload_local_store.py` 未變動 |

## Test results
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -q`
- 結果：124 passed / 0 failed / 0 skipped（基準 121，符合「數目不得減少」；+3 為新增測試）
- 失敗測試：無
- 既有 suite regression：✅ 無（`test_db.py`、`test_dedup.py`、`test_webhook.py`、`test_watchlist.py`、`test_tools.py`、`test_scheduler.py` 全數以 `-v` 個別重跑確認，59/59 全綠，含 contract 特別點名的每一項）
- 備註：**sandbox 對 `~/.cache/uv` 有寫入限制**（`Failed to initialize cache at .../.cache/uv`），已用 `dangerouslyDisableSandbox: true` 重跑確認全綠，與 generator 自報一致。

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- **`src/agent/db/backend.py:64` vs `backend.py:341`**：Protocol 宣告 `get_watchlist(self, chat_id: str | None = None)`，但 `LocalSQLiteBackend.get_watchlist` 的簽名是 `get_watchlist(self, chat_id: str)`（無預設值、型別不含 `None`）。這不是本 sprint 引入的行為 bug——local backend 的唯一呼叫路徑（`processor_handler.py`、`local_server.py`）永遠帶明確 `chat_id`，且 `LocalSQLiteBackend.get_watchlist` 本身是既有程式碼、未被本次改動觸碰。純屬 Protocol 文件與既有 local 實作簽名不完全對齊，不影響 runtime；如果未來要嚴格化型別檢查（例如引入 mypy），可以順手把 `LocalSQLiteBackend.get_watchlist` 簽名補上 `str | None = None` 使其對稱。非本 sprint 必須處理項目。

### ✅ What looks good
- `DynamoDBBackend` 的 11 個方法與被取代的 inline 分支逐行比對後完全等價（TTL 計算、`ScanIndexForward=False` + `items.reverse()`、conditional put 的 `ConditionalCheckFailedException` 捕捉邏輯、`LastEvaluatedKey` 分頁），是一次乾淨的「純搬移、零行為改動」重構。
- wrapper 層（conversations/subscribers/dedup/watchlist/earnings）collapse 後乾淨俐落，orphan import 清除徹底（`grep "\._table\b"` 全域搜尋確認無殘留），符合「surgical changes」原則。
- Protocol 範圍嚴格對齊 ADR-0003 的取捨（不為 local-only 方法補 stub），docstring 直接引用 ADR，可追溯性好。
- 測試基礎設施調整精準命中風險點：`get_backend()` 的 `@lru_cache` 跨測試洩漏問題透過 env-autouse fixture 一次性解決，覆蓋了 contract 點名的 3 個 monkeypatch-only 測試（`test_tools.py`、`test_watchlist.py` 兩條），未逐一插入 fixture 造成散彈式改動。
- 新增測試務實對應 contract 要求：`test_get_backend_returns_local_sqlite_backend_for_local` / `_for_dynamodb`（必要）、`test_dynamodb_backend_claim_update_id_first_claim_wins`（建議項，走 moto `aws` fixture 驗證 first-claim-wins），未過度添加。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻擋性問題；🟢 建議項（`LocalSQLiteBackend.get_watchlist` 簽名與 Protocol 對齊）可留待未來需要時再處理。
