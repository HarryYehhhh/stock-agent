# Spec: Storage backend Strategy/Adapter 收斂

## Goal
把散落在各 db wrapper 內的 DynamoDB inline 邏輯收斂成一個 `DynamoDBBackend` class，與既有 `LocalSQLiteBackend` 共用一個 `typing.Protocol` 介面，讓每個持久化操作只需一條 `get_backend().X(...)` 分派，不再兩邊各寫一次。

## User stories
- As a maintainer, I want 兩個 storage backend 實作同一組方法, so that 新增持久化操作只需在 Protocol 加一個方法、兩個 backend 各實作一次，而不是在 wrapper 內再加一條 `if selected_backend_name()` 分支。
- As a maintainer, I want db wrapper function 變成單純的 `get_backend().X(...)` 分派, so that wrapper 層不再重複 DynamoDB 的 boto3 細節，storage 存取邏輯集中在 backend class。

## Acceptance criteria
- [ ] `src/agent/db/backend.py` 定義 `StorageBackend`（`typing.Protocol`，非 `abc.ABC`），宣告所有跨 backend 共用的方法。
- [ ] 新增 `DynamoDBBackend` class，實作與 Protocol 相同的一組方法；conversations / subscribers / dedup 的行為與現行 inline 程式碼逐字等價（put/query/scan/conditional put 與 TTL 計算不變）。
- [ ] `get_backend()` 依 `selected_backend_name()` 回傳對應實例（`local` → `LocalSQLiteBackend`、`dynamodb` → `DynamoDBBackend`），維持 `@lru_cache` singleton 語意；不再對非 local 拋錯。
- [ ] 下列 11 處 `if selected_backend_name() == "local": ... else <inline>` 分支全部收斂成單一 `get_backend().X(...)`：conversations（save_message、get_history）、subscribers（subscribe、unsubscribe、get_all_active）、dedup（claim_update_id）、watchlist（get_watchlist、add_to_watchlist、remove_from_watchlist、seed_watchlist_if_empty）、tools/earnings（upsert_earnings_calendar）。
- [ ] watchlist 在 dynamodb 端行為不變：`get_watchlist` 回傳全域 `DEFAULT_WATCHLIST`（copy）；`add_to_watchlist`/`remove_from_watchlist` 拋 `NotImplementedError`；`seed_watchlist_if_empty` 為 no-op。
- [ ] `upsert_earnings_calendar` 在 dynamodb 端為 no-op（維持現行「dynamodb 不快取 earnings」行為）。
- [ ] DynamoDB table resource 維持快取（不可每次呼叫重建）。
- [ ] 全部既有測試維持綠燈：`PYTHONPATH=src uv run pytest tests/ -q`（目前 121 passed）。

## Non-goals
- 不改任何 wrapper function 的對外簽名與回傳型別；呼叫端（handler / agent / local_server / market_summary）不需改動。
- 不收斂 `scheduler_handler._run()` 的 `local` vs live-fetch 流程分支——那是兩套不同的通知流程（快取版 vs live-fetch 版），非 storage 存取分派。
- 不把 DynamoDB 端的 watchlist / earnings 快取 / news / market_summaries / company_summaries / notifications 做成真正實作（維持現行降級行為）。
- 不改 `selected_backend_name()` 的 backend 選擇邏輯（`STORAGE_BACKEND` / `AWS_LAMBDA_FUNCTION_NAME`）。
- 不動 `upload_local_store.py` 既有的 boto3 上傳路徑。

## Open questions
- 無阻擋性問題。設計取捨（Protocol 只涵蓋「跨 backend 共用」方法、local-only 方法不進 Protocol）記於 ADR-0003。
</content>
</invoke>
