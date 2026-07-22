# QA Report: per-subscriber-watchlist-sprint

- **Contract**: docs/contracts/per-subscriber-watchlist-sprint.md
- **Spec**: docs/specs/per-subscriber-watchlist.md
- **ADR**: docs/decisions/0002-per-subscriber-watchlist-migration.md
- **Date**: 2026-07-22
- **Verdict**: ⚠️ Pass with issues

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| local `watchlist` 表改 junction shape（PK `(chat_id, symbol)`）+ `idx_watchlist_symbol` | ✅ | `backend.py:83-99`；`tests/test_watchlist.py::test_local_store_creates_junction_watchlist_schema` |
| `ticker` → `symbol` 全面改名 | ✅ | 僅剩 migration 讀舊表欄位（`backend.py:189,215`）與工具參數說明字串（`symbol` 描述含 "ticker" 詞，非 watchlist 命名），符合 spec |
| Migration：舊 shape 偵測、綁定既有 subscriber、無 subscriber 丟棄 | ✅ | `_migrate_watchlist_schema()`；`test_migration_copies_old_global_watchlist_to_each_subscriber`、`test_migration_discards_old_rows_when_no_subscribers` |
| Migration idempotent | ✅ | 同一測試內驗證重建 backend 不重複/不覆寫 |
| `get_watchlist(chat_id)` per-user、依 position 排序 | ✅ | `backend.py:286-297` |
| 新增一檔（大寫、position+1、重複 no-op） | ✅ | `add_to_watchlist`；`test_add_to_watchlist_normalizes_uppercase_and_assigns_position`、`test_add_to_watchlist_duplicate_is_noop` |
| 移除一檔（回傳是否刪除） | ✅ | `remove_from_watchlist`；`test_remove_from_watchlist_reports_whether_removed` |
| seed-if-empty | ✅ | `seed_watchlist_if_empty`；`test_seed_if_empty_writes_default_watchlist`、`test_seed_if_empty_does_not_overwrite_existing_watchlist` |
| per-subscriber 通知 join（含必要欄位） | ✅ | `get_watchlist_earnings_for_date`；`test_watchlist_earnings_join_and_notification_tracking` |
| active subscriber watchlist distinct symbol 方法 | ✅ | `get_active_watchlist_symbols` |
| 移除 `_seed_watchlist()` 全域覆蓋 | ✅ | 已無殘留呼叫；`test_custom_watchlist_survives_backend_reconstruction` |
| dynamodb `get_watchlist(chat_id)` 回全域 DEFAULT_WATCHLIST | ✅ | `watchlist.py:39-43`；`test_get_watchlist_uses_code_defaults_for_dynamodb` |
| dynamodb add/remove raise `NotImplementedError` | ✅ | `test_add_remove_raise_not_implemented_for_dynamodb` |
| scheduler 同步階段改抓 active watchlist 聯集 | ✅ | `_sync_earnings_calendar()` 用 `get_active_watchlist_symbols()` |
| scheduler 通知階段 per-subscriber join + unnotified + summary + send + mark | ✅ | `_notify_local()`；`test_local_per_subscriber_push_only_own_symbols_and_is_idempotent` |
| Finnhub 失敗 stale banner + 快取降級 | ✅ | 邏輯未動；`test_local_warns_and_uses_cache_when_finnhub_fails` |
| dynamodb `_notify_live()` 行為不變 | ✅ | 僅欄位改名，既有 dynamodb 測試全過 |
| `/watchlist` | ✅ | 空清單給提示＋用法；`test_watchlist_empty_gives_usage_hint` |
| `/watch <SYMBOL>` | ⚠️ | 功能運作、大寫正規化、缺參數/多字給用法說明皆通過測試；但「格式無效」僅檢查空字串與含空白，未驗證 symbol 字元集，見 🟡 finding |
| `/unwatch <SYMBOL>` | ✅ | 同上邏輯，移除不存在項回非錯誤語氣 |
| dynamodb 下 add/remove 攔截 NotImplementedError 優雅回覆 | ✅ | `test_watch_and_unwatch_are_graceful_on_dynamodb_backend` |
| `/subscribe` seed-if-empty | ✅ | `test_subscribe_seeds_default_watchlist_for_new_chat`、`test_subscribe_does_not_overwrite_existing_custom_watchlist` |
| WELCOME 文案更新 | ✅ | `test_welcome_mentions_watchlist_commands` |

## Test results
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 結果：110 passed, 0 failed, 0 skipped
- 失敗測試：無
- 既有 suite regression：✅（`test_scheduler.py` dynamodb 案例、`test_storage_backend.py`、`test_upload_local_store.py` 等既有測試全部維持綠燈）

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
- **`src/agent/processor_handler.py:83-88`（`/watch`）與 `:100-105`（`/unwatch`）**：contract 要求「缺參數或格式無效 → 用法說明」，但目前唯一的格式檢查是「空字串」與「含空白」，對其他無效格式（例如特殊符號、超長字串）一律直接大寫後寫入 watchlist，不會回用法說明。已用手動驗證重現：
  ```
  /watch $$$weird!!
  → ✅ 已將 $$$WEIRD!! 加入追蹤清單
  ```
  這類 symbol 之後不會匹配任何 `earnings_calendar` row，實務影響有限（不會造成通知錯誤或崩潰），但與 contract 文字「格式無效 → 用法說明」不完全一致，且使用者的 `/watchlist` 會出現無意義項目。
  建議修法：在 `symbol = arg.upper()` 後加一個輕量格式檢查（例如 `re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol)`），不符合就回 `WATCH_USAGE` / `UNWATCH_USAGE`。若 team 認為目前「僅擋空白／空字串」已足夠涵蓋 contract 語意，也可以在 contract/spec 中明確澄清「格式無效」的定義，把此項改為已知邊界行為。

### 🟢 Suggestions
- **Tests required 清單**中「無效參數回用法說明」目前只涵蓋「缺參數（空字串）」，未有測試涵蓋「多字參數」（例如 `/watch AAPL MSFT`）這個目前程式碼確實有處理的分支（`" " in arg` 判斷）。建議補一條測試鎖住這個行為，避免未來重構時不小心鬆綁。
- `src/agent/processor_handler.py` 的 `/watch` 與 `/unwatch` 兩段（84-98 行、101-115 行）結構高度相似（取參數→驗證→呼叫 backend→NotImplementedError 攔截→回覆），目前規模下重複尚可接受，不需要為兩個呼叫點提前抽象；僅記錄以供未來若再新增第三個 watchlist 指令時考慮共用 helper。
- `docs/decisions/0002-per-subscriber-watchlist-migration.md` 提到的 trade-off（`_init_schema` 對 subscribers 表初始化順序有隱含依賴）已在 `backend.py` 中正確處理（`subscribers` 先於 `_migrate_watchlist_schema()` 建立），但程式碼內沒有註解特別強調「不可調換順序」，未來若有人重排 `_init_schema()` 內的建表順序容易不小心破壞這個隱含依賴。可考慮加一行註解提醒。

### ✅ What looks good
- Schema、migration、backend 方法、scheduler join、processor 指令，逐條核對 contract 與 spec acceptance criteria 均落地，且都有對應測試覆蓋（非同義反覆的測試，例如 per-subscriber 推播測試確實驗證了「兩個 subscriber 各自只收到自己 symbol」與「重跑不重複」兩個關鍵行為）。
- Migration 邏輯正確處理「有 subscriber」「無 subscriber」「已是新 shape」三種情境，且用真實的 SQL 建表方式模擬舊 DB（非 mock），測試可信度高。
- `add_to_watchlist` / `remove_from_watchlist` 皆使用參數化 SQL，無 SQL injection 疑慮；symbol 正規化（大寫）在 backend 層與 processor 層都做了（略有重複但無害）。
- DynamoDB 路徑刻意維持最小改動（僅欄位改名），`_notify_live()` 相關既有測試全數維持綠燈，符合 spec「本 sprint DynamoDB 延後」的決策。
- `upload_local_store.py`（out of scope 明確排除 watchlist）未被誤動，符合 contract 的 Out of scope 條款。
- 找不到殘留的 `_seed_watchlist()` 呼叫或舊「每次啟動覆蓋 watchlist」邏輯。

## 結論
- 是否可合併：是（🟡 為非阻塞性瑕疵，可合併後補測試/追加澄清，也可視需要在下一輪小改）
- 最該先處理的 1–3 件事：
  1. 釐清並視需要修正 `/watch`、`/unwatch` 的「格式無效」判定範圍（`src/agent/processor_handler.py:83-105`）。
  2. 補一條測試鎖住「多字參數回用法說明」這個既有分支行為。
  3.（可選）在 `_init_schema()` 附近加註解說明 `subscribers` 表必須先於 `_migrate_watchlist_schema()` 建立，降低未來重排順序的風險。
