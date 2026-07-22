# Contract: storage-backend-strategy-sprint

對應 spec: docs/specs/storage-backend-strategy.md
相關 ADR: docs/decisions/0003-storage-backend-strategy.md

這是**內部重構**，不改對外行為。每一項 deliverable 完成後，既有測試必須仍然全綠。

## Deliverables

### D1 — 定義 `StorageBackend` Protocol（backend.py）
- [ ] 在 `src/agent/db/backend.py` 用 `typing.Protocol`（**不要** `abc.ABC`）定義 `StorageBackend`，宣告以下 11 個方法（簽名對齊 `LocalSQLiteBackend` 現有方法）：
  - `save_message(chat_id: str, role: str, content: str) -> None`
  - `get_history(chat_id: str, limit: int) -> list[dict]`
  - `subscribe(chat_id: str) -> None`
  - `unsubscribe(chat_id: str) -> None`
  - `get_all_active() -> list[str]`
  - `claim_update_id(update_id: int) -> bool`
  - `get_watchlist(chat_id: str | None = None) -> list[dict]`
  - `add_to_watchlist(chat_id: str, symbol: str, exchange: str | None = None) -> bool`
  - `remove_from_watchlist(chat_id: str, symbol: str) -> bool`
  - `seed_watchlist_if_empty(chat_id: str) -> None`
  - `upsert_earnings_calendar(items: list[dict]) -> None`
- [ ] Protocol **只涵蓋跨 backend 共用的方法**。local-only 方法（`get_active_watchlist_symbols`、`get_watchlist_earnings_for_date`、`get_chat_watchlist_earnings`、`unnotified_symbols`、`mark_notified`、`get_company_summary`、`upsert_company_summary`、`insert_news`、`list_news_since`、`insert_market_summary`、`export_rows`）**維持只在 `LocalSQLiteBackend` 上，不進 Protocol、不在 `DynamoDBBackend` 實作**（理由見 ADR-0003；這些 code path 只在 local backend 執行）。

### D2 — 新增 `DynamoDBBackend` class（backend.py）
- [ ] 在 `src/agent/db/backend.py` 新增 `DynamoDBBackend`，實作 D1 的 11 個方法。實作內容**逐字移植**現有 wrapper 內的 inline 程式碼：
  - `save_message`：`_table().put_item(Item={chat_id, timestamp=isoformat, role, content, ttl=time()+CONVERSATION_TTL_SECONDS})`（來源 `conversations.py:38-46`）。
  - `get_history`：`query(KeyConditionExpression=Key("chat_id").eq(chat_id), ScanIndexForward=False, Limit=limit)` → `items.reverse()` → return（來源 `conversations.py:54-61`）。
  - `subscribe`：`put_item(Item={chat_id, subscribed_at=isoformat, active=True})`（來源 `subscribers.py:24-30`）。
  - `unsubscribe`：`update_item(Key={chat_id}, UpdateExpression="SET active = :a", ExpressionAttributeValues={":a": False})`（來源 `subscribers.py:38-42`）。
  - `get_all_active`：`scan(FilterExpression="active = :a", ...=True)` 含 `LastEvaluatedKey` 分頁（來源 `subscribers.py:50-63`）。
  - `claim_update_id`：conditional `put_item(ConditionExpression="attribute_not_exists(update_id)")`，捕捉 `ClientError` 的 `ConditionalCheckFailedException` → `False`，其餘 re-raise（來源 `dedup.py:30-39`）。
  - `get_watchlist(chat_id=None)`：回傳 `[item.copy() for item in DEFAULT_WATCHLIST]`（忽略 `chat_id`）。用 **lazy import**（`from agent.db.watchlist import DEFAULT_WATCHLIST`，函式內 import）避免與 `watchlist.py` 循環匯入，比照現有 `seed_watchlist_if_empty` 的做法。
  - `add_to_watchlist` / `remove_from_watchlist`：`raise NotImplementedError("watchlist management is only supported on the local backend")`（維持 `watchlist.py:53,63` 現行訊息）。
  - `seed_watchlist_if_empty`：no-op（現行 dynamodb 端不做事）。
  - `upsert_earnings_calendar`：no-op（現行只有 local 快取）。
- [ ] **Table 快取**：三張表（conversations / subscribers / processed_updates）在 `DynamoDBBackend` 內建立一次並持有（例如 `__init__` 內以 `boto3.resource("dynamodb", region_name=aws_region()).Table(...)` 建立後存為 instance 屬性），後續呼叫重用同一 Table handle，不得每次呼叫重建。singleton 語意由外層 `get_backend()` 的 `@lru_cache` 提供。表名取自 `agent.config` 的 `conversations_table()` / `subscribers_table()` / `processed_updates_table()`。
- [ ] **約束**：`DynamoDBBackend.__init__` 與其 no-op 方法（`seed_watchlist_if_empty`、`upsert_earnings_calendar`）**不得執行任何 DynamoDB 網路操作**。`boto3.resource(...).Table(name)` 本身是離線 lazy 建構，可接受；no-op 方法必須真的什麼都不做。（原因：`test_tools.py` 在 `STORAGE_BACKEND=dynamodb` 且**無 moto/無建表**下呼叫 `get_earnings_calendar`，收斂後會走到 `get_backend().upsert_earnings_calendar(...)`——必須不觸網才能維持綠燈。）

### D3 — 改寫 `get_backend()`（backend.py）
- [ ] `get_backend()` 改為依 `selected_backend_name()` 回傳對應實例：`local` → `LocalSQLiteBackend(local_store_path())`；`dynamodb` → `DynamoDBBackend()`。移除現行「非 local 就 `raise ValueError`」。
- [ ] 維持 `@lru_cache(maxsize=1)`。回傳型別標註為 `StorageBackend`。
- [ ] 更新 module docstring（目前寫「Storage backend selection and local sqlite implementation」）以涵蓋 dynamodb 實作。

### D4 — 收斂 11 處分支（wrapper 層）
把下列每一處的 `if selected_backend_name() == "local": get_backend().X(...) else <inline dynamodb>` 收斂成單一 `get_backend().X(...)`，並刪除因此變成 orphan 的 import 與 module-level `@lru_cache _table()`：
- [ ] `src/agent/db/conversations.py`：`save_message`、`get_history` → 單一分派。刪除 `_table()`、`boto3`、`Key`、`selected_backend_name`、`CONVERSATION_TTL_SECONDS`、`time`、`datetime` 等因收斂而未使用的 import。`build_messages` 不變。
- [ ] `src/agent/db/subscribers.py`：`subscribe`、`unsubscribe`、`get_all_active` → 單一分派。刪除 orphan import（`_table`、`boto3`、`datetime`、`selected_backend_name`、`aws_region`、`subscribers_table` 等）。
- [ ] `src/agent/db/dedup.py`：`claim_update_id` → 單一分派。刪除 orphan import（`_table`、`boto3`、`ClientError`、`DEDUP_TTL_SECONDS`、`time`、`selected_backend_name` 等）。
- [ ] `src/agent/db/watchlist.py`：`get_watchlist`、`add_to_watchlist`、`remove_from_watchlist`、`seed_watchlist_if_empty` → 單一分派。**保留** `DEFAULT_WATCHLIST`（仍是 single source of truth）。刪除 `selected_backend_name` import（若不再使用）。
- [ ] `src/agent/tools/earnings.py`：`upsert_earnings_calendar` 分支 → 無條件 `get_backend().upsert_earnings_calendar(items)`。刪除 `selected_backend_name` import。
- [ ] 收斂後 `grep -rn "selected_backend_name()" src/agent/db src/agent/tools` 中，除 `backend.py` 內的定義/使用外，wrapper 層應為 0 處分派用途。

### D5 — 測試基礎設施調整（conftest 與 monkeypatch-only 測試）
- [ ] `tests/conftest.py` 的 `clear_db_caches()`：移除已刪除的 `dedup._table.cache_clear()`、`conversations._table.cache_clear()`、`subscribers._table.cache_clear()`（這些函式已不存在，不移除會 `AttributeError`）。保留 `cfg._load_secrets.cache_clear()`、`backend.get_backend.cache_clear()`、`webhook_handler._sqs.cache_clear()`。一併清掉因此變成未使用的 import。
- [ ] **防止 backend singleton 跨測試洩漏**：`get_backend()` 現在會被 `test_tools.py`（`mock_http` fixture 設 `STORAGE_BACKEND=dynamodb`）與 `test_watchlist.py`（`test_get_watchlist_uses_code_defaults_for_dynamodb`、`test_add_remove_raise_not_implemented_for_dynamodb`，均以 monkeypatch 設 dynamodb、不使用 `local_store`/`aws` fixture）首次呼叫到。收斂前這些測試不碰 `get_backend()`，收斂後會。為避免 `@lru_cache` 回傳上一個測試殘留的 backend 實例，**在 `tests/conftest.py` 的 autouse `env` fixture 內加一行 `backend.get_backend.cache_clear()`（於每個測試開始前清空）**。這是最小且確定性的做法；不要靠改各別測試逐一插入清快取。
  - 允許的替代做法：在上述 3 個 monkeypatch-only 測試各自呼叫 `clear_db_caches()`。但優先採用 env-autouse 清快取（同時涵蓋 `test_tools.py`）。

## Out of scope
- `scheduler_handler._run()`（`scheduler_handler.py:194`）的 `local` vs live-fetch 流程分支——保留不動（非 storage 分派）。
- `upload_local_store.py` 的 boto3 上傳路徑——不動（`main()` 內 `selected_backend_name()` 只是驗證用途，保留）。
- 不為 dynamodb 端補齊 watchlist / earnings 快取 / news / summaries 的真實實作。
- 不動 `DEFAULT_WATCHLIST` 內容、不動 handler / agent / local_server / market_summary 的呼叫端。

## Tests required
- [ ] 既有測試全綠：`PYTHONPATH=src uv run pytest tests/ -q`（基準 121 passed，收斂後數目不得減少）。特別確認以下既有測試仍過，因為它們現在改走 `DynamoDBBackend`：
  - `tests/test_db.py`（save/query history、subscribe/unsubscribe，走 moto `aws` fixture）
  - `tests/test_dedup.py`（conditional put first-claim-wins，走 moto）
  - `tests/test_webhook.py`（claim_update_id 走 moto）
  - `tests/test_watchlist.py::test_get_watchlist_uses_code_defaults_for_dynamodb`、`::test_add_remove_raise_not_implemented_for_dynamodb`
  - `tests/test_tools.py` 全部（earnings 在 dynamodb 無 moto 下走 `upsert_earnings_calendar` no-op）
  - `tests/test_scheduler.py`（dynamodb `mocks` 與 local `local_mocks` 兩組）
- [ ] 新增 1 條測試：驗證 `get_backend()` 在 `STORAGE_BACKEND=local` 回傳 `LocalSQLiteBackend`、在 `=dynamodb` 回傳 `DynamoDBBackend`（可放 `tests/test_storage_backend.py`）。
- [ ] （建議）新增 1 條測試：`DynamoDBBackend` 走 moto `aws` fixture，直接呼叫 `get_backend().claim_update_id(...)` 兩次驗證 first-claim-wins，作為 backend class 層級的顯式覆蓋。

## Definition of done
- 全部 deliverable 打勾。
- `PYTHONPATH=src uv run pytest tests/ -q` 全綠（>= 121 passed）。
- `grep -rn "selected_backend_name()"` 在 wrapper（conversations/subscribers/dedup/watchlist/tools/earnings）中無殘留分派分支。
- evaluator 的 QA report 無 P0/P1。
</content>
