# ADR-0003: Storage backend Strategy/Adapter 抽象層

- **Date**: 2026-07-22
- **Status**: Accepted

## Context
ADR-0001 引入了 local-first backend 邊界：`selected_backend_name()` 決定用 `LocalSQLiteBackend` 還是 DynamoDB。但當時 DynamoDB 那半邊的邏輯留在各 db wrapper function 內（`conversations.py` / `subscribers.py` / `dedup.py` / `watchlist.py` 與 `tools/earnings.py`），每個 function 都是：

```python
if selected_backend_name() == "local":
    return get_backend().X(...)
# else: inline boto3/dynamodb 程式碼
```

結果：
- 兩個 backend 不對稱——local 是一個內聚 class，dynamodb 是散落的 inline 片段。
- 目前有 11 處這種分支；每加一個持久化操作要寫兩次並多加一條分支。
- `get_backend()` 只服務 local，對 dynamodb 直接 `raise`，強化了不對稱。

## Decision
在 database layer 引入 Strategy/Adapter 抽象：

1. 用 `typing.Protocol` 定義 `StorageBackend`（**不用** `abc.ABC`——沿用專案偏好的結構型別、避免強制繼承，兩個既有 class 免改繼承宣告即滿足介面）。
2. 把 DynamoDB 半邊收斂成 `DynamoDBBackend` class，實作與 Protocol 相同的一組方法。
3. `get_backend()` 依 `selected_backend_name()` 回傳對應實例，維持 `@lru_cache` singleton；不再對非 local 拋錯。
4. 各 wrapper function 收斂成單一 `get_backend().X(...)` 分派。

**Protocol 範圍**：只涵蓋「跨 backend 共用」的 11 個方法（conversations / subscribers / dedup / watchlist / earnings upsert）。local-only 方法（earnings 快取 join、news、market_summaries、company_summaries、notifications、export_rows）**維持只在 `LocalSQLiteBackend`，不進 Protocol、不在 `DynamoDBBackend` 補 stub**——因為這些 code path 只在 local backend 執行（scheduler 的 `_notify_local`/`sync_earnings`、`market_summary.py`、`local_server.py` 都是 local-only 流程）。

**degenerate 行為維持不變**：dynamodb 端 `get_watchlist` 回傳全域 `DEFAULT_WATCHLIST`；`add/remove_from_watchlist` 拋 `NotImplementedError`；`seed_watchlist_if_empty` 與 `upsert_earnings_calendar` 為 no-op。

## Consequences
正面：
- 兩個 backend 對稱，新增持久化操作只需在 Protocol 加一個方法、兩個 class 各實作一次。
- wrapper 層退化為單純分派，DynamoDB 的 boto3 細節集中在 `DynamoDBBackend`。
- `get_backend()` 成為唯一的 backend 取得點，語意一致。

負面 / trade-off：
- `get_backend()` 回傳型別是 `StorageBackend`（Protocol），而 scheduler local 流程 / `market_summary` / `local_server` 會在回傳值上呼叫 **local-only** 方法。runtime 正確（那些流程只跑 local backend），但在嚴格靜態型別檢查下會被視為存取 Protocol 未宣告的方法。專案目前無 mypy gate，接受此取捨；必要時呼叫端可自行標註為 `LocalSQLiteBackend`。
- `get_backend()` 現在會被更多測試路徑首次觸及（含 monkeypatch 切 dynamodb 的測試），`@lru_cache` singleton 需要更嚴謹的跨測試清快取（於 conftest autouse fixture 每測試前 `cache_clear()`）。

## Alternatives considered
- **維持現狀（inline 分支）**：改動最小，但不對稱與重複的問題持續累積，每個新操作都要複製分支。
- **全對稱 Protocol（DynamoDBBackend 為所有 local-only 方法補 `NotImplementedError` stub）**：介面完全對稱，但要新增約 11 個永不被呼叫的 raise stub，違反「不為不可能情境寫 error handling」；且會誤導讀者以為 dynamodb 有意支援這些操作。故選擇「Protocol 只涵蓋跨 backend 共用方法」。
- **用 `abc.ABC` + 繼承**：可在實例化時強制檢查方法齊全，但需要兩個 class 顯式繼承、與專案既有的 duck-typing 風格不符，且對 `LocalSQLiteBackend` 是侵入式改動。Protocol 提供同等的介面文件價值而無繼承耦合。
- **拆成獨立 `dynamodb_backend.py` 模組**：可讓 `backend.py` 不引入 boto3/config。但 `config.py` 不 import `backend.py`（無循環風險），且 wrapper 已從 `agent.db.backend` 匯入 `get_backend`，把兩個 class 放同一模組匯入面最單純，故選擇留在 `backend.py`。
</content>
