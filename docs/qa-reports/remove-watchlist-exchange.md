# QA Report: remove-watchlist-exchange

- **需求**：從 watchlist 完全移除 exchange 欄位（範圍限定 src/、tests/）
- **Date**: 2026-07-24
- **Verdict**: ✅ Pass

## 需求檢查清單

| 項目 | Status | Note |
|---|---|---|
| `db/watchlist.py` DEFAULT_WATCHLIST 每筆只留 symbol | ✅ | 28 筆全部改為 `{"symbol": ...}` |
| `db/backend.py` Protocol / LocalSQLiteBackend / DynamoDBBackend 的 `add_to_watchlist` 拿掉 exchange 參數 | ✅ | 三處簽名一致改為 `(chat_id, symbol) -> bool` |
| watchlist CREATE TABLE 拿掉 exchange 欄位 | ✅ | `CREATE TABLE IF NOT EXISTS watchlist` 已無 exchange 欄 |
| 輕量 migration：既有 DB 含 exchange 欄就 DROP COLUMN | ✅ | `PRAGMA table_info` 偵測 + `ALTER TABLE watchlist DROP COLUMN exchange`，SQLite 3.50.4 支援（DROP COLUMN 自 3.35 起支援），已用 mock 舊 schema 的測試覆蓋 |
| `_migrate_watchlist_schema`（舊 global→per-subscriber）移除 exchange | ✅ | `SELECT ticker, position`、新 CREATE TABLE、INSERT 均已移除 exchange |
| `get_watchlist` / `seed_watchlist_if_empty` / `get_active_watchlist_symbols` / `get_watchlist_earnings_for_date` / `get_chat_watchlist_earnings` 移除 exchange | ✅ | 六個查詢方法 SELECT 清單均已移除 exchange 欄位/別名 |
| `scheduler_handler.py` PROMPT_TEMPLATE / COMPANY_PROMPT_TEMPLATE / `_company_summary` / `_notify_live` 移除 exchange | ✅ | 兩個 prompt 文字、`_company_summary` 的 format 參數與 fallback 訊息、`_notify_live` 的 `earnings.extend(rows)`（不再手動塞 exchange）均已處理 |
| `processor_handler.py` `/watchlist` 顯示只留 symbol | ✅ | `f"• {item['symbol']}"`，移除交易所後綴邏輯 |
| 對應測試更新 | ✅ | `test_watchlist.py`、`test_storage_backend.py`、`test_scheduler.py`、`test_local_integration.py`（exchange 相關斷言部分）均同步移除；新增 `test_migration_drops_exchange_column_from_existing_per_subscriber_db` 覆蓋 DROP COLUMN migration |

## 測試結果

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內：131 passed / 12 errors（既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關，歷次 QA report 皆有記載此已知限制）
- 停用沙盒重跑：**143 passed, 0 failed**
- 既有 suite regression：✅ 無

## grep 覆核

- `grep -rn "exchange" src/ tests/`：僅剩三處，皆合理保留
  - `backend.py:141,145,146` — DROP COLUMN migration 本身（為了移除 exchange 而存在的程式碼）
  - `backend.py:231` — `_migrate_watchlist_schema` docstring，描述「歷史舊 schema」形狀（`ticker PRIMARY KEY, exchange, position`），屬正確的歷史紀錄，非殘留
  - `tests/test_watchlist.py:86,134,157` — 用原始 SQL 建立「遷移前舊 schema」的測試 fixture，模擬真實舊資料以驗證 migration 行為，非殘留
- `grep -rn "NASDAQ\|NYSE" src/ tests/`：僅剩 `test_watchlist.py` 三處，同樣是舊 schema fixture 資料，非殘留
- `docs/specs/`、`docs/contracts/`、`docs/decisions/` 等歷史文件仍含 exchange 字樣（描述當初設計決策），使用者已明確將範圍限定在 `src/` 與 `tests/`，未改動這些文件符合指示，非缺陷

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- **`src/agent/db/backend.py:141-146`**：DROP COLUMN migration 放在 `CREATE TABLE IF NOT EXISTS watchlist` 之後執行順序正確（先確保新 schema 表存在，再偵測舊欄位補刀），但依賴 SQLite ≥ 3.35（2021-03）的 `DROP COLUMN` 支援。專案其餘 migration 皆用「新建表＋搬資料」模式（見 `_migrate_watchlist_schema`），這處改用原生 `DROP COLUMN` 是更精簡的寫法，行為上沒有問題，僅供之後若要相容更舊 SQLite 版本時留意。不影響本次驗收。
- **歷史文件（`docs/specs/`、`docs/contracts/`、`docs/decisions/0002-per-subscriber-watchlist-migration.md`）** 仍描述 exchange 欄位，這是設計決策的歷史紀錄，使用者已明確排除在本次範圍外，僅供之後若有人依文件重建 schema 時參考本次變更。

### ✅ What looks good
- 六個檔案的改動完全對應使用者盤點的 5 個點，沒有夾帶無關改動（Makefile / local_server.py / market_summary.py / docs/deployment-flow.md 的既有 diff 經確認是本 session 之前就存在、與本次任務無關的其他 phase 未提交工作，本次未再觸碰）
- Migration 邏輯正確且有雙重覆蓋：（1）舊 global watchlist（無 chat_id）→ per-subscriber 遷移，同時移除 exchange；（2）新版 per-subscriber schema 但仍殘留 exchange 欄位的既有 DB → DROP COLUMN。兩條路徑互斥（`_migrate_watchlist_schema` 靠 `chat_id` 欄位存在與否短路），新增的 `test_migration_drops_exchange_column_from_existing_per_subscriber_db` 精準覆蓋第二條路徑，且用 `PRAGMA table_info` 斷言欄位集合而非只驗證行為，測試品質好
- `_notify_live` 從 `earnings.extend({**row, "exchange": item["exchange"]} for row in rows)` 簡化為 `earnings.extend(rows)`，行為正確（不再需要手動從 watchlist item 補 exchange 到 earnings row）
- DynamoDBBackend 與 LocalSQLiteBackend 的 `add_to_watchlist` 簽名保持一致（Protocol 定義同步更新），沒有出現兩個實作簽名不一致的問題
- SQL 正確性：所有 SELECT/INSERT 語句欄位數與佔位符數量一致，未見遺漏或多餘的 `?`

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無（無 🔴/🟡，兩個 🟢 皆為資訊性建議，不影響合併）
