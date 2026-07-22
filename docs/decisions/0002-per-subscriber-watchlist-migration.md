# ADR-0002: Per-subscriber watchlist schema migration

- **Date**: 2026-07-22
- **Status**: Proposed

## Context
既有 local `watchlist` 表是全域單一清單（`ticker TEXT PRIMARY KEY, exchange, position`），並在每次 backend 建構時用 `DEFAULT_WATCHLIST` 強制覆蓋。本 sprint 要改成 per-subscriber junction table（`chat_id, symbol, exchange, position, PRIMARY KEY (chat_id, symbol)`），同時把 `ticker` 欄位/命名改名為 `symbol` 對齊 `earnings_calendar.symbol`。

問題：既有 local DB 已可能有 active subscriber 依賴目前的（全域）watchlist 收通知。schema 從「無 owner 的全域表」轉成「以 chat_id 為 key 的個人表」是資料形狀改變，必須決定既有全域 rows 如何落到新表，否則既有 subscriber 會在升級後靜默失去 watchlist 與每日通知。此決策影響資料保存、升級安全性與後續 DynamoDB 設計方向，屬不可逆的資料模型改動。

## Decision
1. **就地 migration，綁定既有 subscriber**：在 `_init_schema()` 偵測舊 shape（`watchlist` 表存在且無 `chat_id` 欄位）時一次性遷移——讀出舊全域 rows，對 `subscribers` 表中每個 `chat_id` 複製一份等同的個人清單（`symbol = ticker`，保留 `exchange`、`position`）。無 subscriber 時舊 rows 丟棄，之後 `/subscribe` 以 `DEFAULT_WATCHLIST` seed。
2. **欄位改名 `ticker` → `symbol`** 一併於此 migration 完成，程式碼層命名同步改為 `symbol`。
3. **Migration idempotent**：僅在偵測到舊 shape 時執行；新 shape 直接略過，不覆寫既有（含使用者自訂）資料。
4. **移除全域強制 seed**：改為 `/subscribe` 時對「尚無 watchlist 的使用者」seed-if-empty，避免每次啟動洗掉自訂項目。
5. **DynamoDB 延後**：per-user watchlist 的 DynamoDB 儲存本 sprint 不做，僅記錄設計方向：新表 `PK = chat_id, SK = symbol`（item 帶 `exchange`、`position`），反向查詢用 `symbol` 為 PK 的 GSI。

## Consequences
正面：
- 既有 subscriber 升級後不掉 watchlist、不漏通知。
- 命名對齊 `symbol`，join / 閱讀一致。
- Seed-if-empty 讓自訂項目穩定存活，移除「每次啟動被覆蓋」的既有缺陷。

負面 / trade-off：
- Migration 在 backend 建構時讀 `subscribers` 表，`_init_schema` 對表初始化順序有隱含依賴（subscribers 表需先於 watchlist migration 建立）；需在實作中確保順序。
- Per-subscriber summary 讓 scheduler 的 `run_agent` 呼叫次數隨 pending subscriber 數成長（見 spec Open questions）。
- 全域與個人模型分叉：local 為 per-user，dynamodb 暫維持全域 `DEFAULT_WATCHLIST`，兩路徑行為短期不一致，待未來 DynamoDB sprint 收斂。

## Alternatives considered
- **丟棄舊表、要求重新 /subscribe**：實作最簡，但既有 subscriber 會靜默失去通知，違反升級安全，不採用。
- **保留全域表、僅加一層 per-user override**：新增複雜度且語意混亂（全域 + 個人兩份來源），join 與去重都變複雜，不採用。
- **一併實作 DynamoDB per-user watchlist**：範圍過大、超出本 local-first sprint；先記錄設計方向，留待後續 ADR / sprint。
- **獨立 migration script（非 `_init_schema` 內建）**：需要額外手動步驟與 reset 指令，專案目前沒有 migration runner；就地偵測式 migration 與現有 `earnings_calendar.updated_at` migration 風格一致，較輕量。
