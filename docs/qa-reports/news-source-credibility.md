# QA Report: news-source-credibility

- **Contract**: 無（feature skill，直接對照使用者需求 + 程式碼品質）
- **Date**: 2026-07-28
- **Verdict**: ✅ Pass

## 需求 checklist

| 項目 | Status | Note |
|---|---|---|
| 1. `refresh_news()` 入庫時保留 Finnhub 的 `source`（Big7 + AI 兩條線都要） | ✅ | `market_summary.py:145-155`（Big7 線）與 `market_summary.py:170-180`（AI 線）都從 `it.get("source")` 讀出並存入 `items` dict 的 `source` key |
| 2. `high_credibility` 白名單標記（Reuters/Dow Jones/AP/CNBC/FT/WSJ/Bloomberg，完全比對、不分大小寫、缺失→False） | ✅ | `tools/news.py:90-111` `HIGH_CREDIBILITY_SOURCES` 為 casefold 後的 frozenset，含要求的 7 家 + 合理別名（`AP`、`Wall Street Journal`）；`is_high_credibility_source()` 用 `source.casefold() in HIGH_CREDIBILITY_SOURCES` 做完全比對（非 substring），`source` falsy 時提前回 False |
| 3. 入庫不過濾，select_news / 摘要邏輯不動 | ✅ | `select_news()`（`market_summary.py:195-210`）與 `_format_news_block()`（`257-264`）程式碼未被改動，仍只依 `category`/`symbol` 篩選，不參照 `source`/`high_credibility`；非白名單 source（如 "Yahoo"）在測試中確認正常入庫（`test_refresh_news_stores_source_and_high_credibility_flag`） |
| 4. `LocalSQLiteBackend` news 表加欄位 + 既有 DB schema 遷移 | ✅ | `backend.py:254-270`：`CREATE TABLE IF NOT EXISTS` 新增兩欄，並比照既有 `earnings_calendar.updated_at` 的遷移模式（`PRAGMA table_info` 檢查 + 缺欄位才 `ALTER TABLE`）補齊舊 DB；`ALTER TABLE ... ADD COLUMN high_credibility INTEGER NOT NULL DEFAULT 0` 是合法 SQLite 語法（常數 DEFAULT）。專門測試 `test_news_schema_migration_of_pre_existing_store` 手刻舊 schema + 舊資料，驗證遷移後舊列 `source=None`、`high_credibility=False`，且新 insert/read 正常 |
| 5. `upload_local_store.py` 的 `_news_item` 映射帶新欄位；DynamoDB schemaless 不改 infra | ✅ | `upload_local_store.py:75-85`：`high_credibility` 固定寫入（`bool(row.get(...))`），`source` 比照既有 `symbol`/`url` 的「非 None 才寫入」模式；沒有動 `infra/template.yaml` |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：477 passed，13 個 `test_local_integration.py` 的 `ThreadingHTTPServer` socket bind `PermissionError`（沙盒網路限制導致，與本次改動無關，非本次新增測試）
- 停用沙盒重跑：**490 passed**，0 failed，0 skipped（與 generator 自報數字一致）
- 新增/異動測試：
  - `tests/test_tools.py`（+3 組 parametrize）：白名單命中（含大小寫變化與別名 `AP`/`Wall Street Journal`）、非白名單與 substring 陷阱（`Bloomberg Law`、`via Bloomberg`、`Bloomberg News Wire` 全部斷言 False）、`None`/空字串
  - `tests/test_market_summary.py`（+1）：`refresh_news()` 同時驗證 Big7 線與 AI 線都正確存 `source`/`high_credibility`，含無 `source` 欄位的 fallback
  - `tests/test_storage_backend.py`（+5）：SQLite insert/read、省略 source/high_credibility 時的預設值、舊 schema 遷移、DynamoDB insert/read 對應案例
  - `tests/test_upload_local_store.py`（+1）：`_news_item` 映射帶新欄位進 DynamoDB
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical（Must Fix）
無

### 🟡 Warnings（Should Fix）
無

### 🟢 Suggestions

- **`tools/news.py:90-103`**：白名單目前用單一 `frozenset` 存所有名稱含別名（`"AP"`、`"Wall Street Journal"` 各自獨立於 `"Associated Press"`、`"The Wall Street Journal"`），沒有結構化的「正式名稱 → 別名清單」映射（不像同檔案裡 `BIG7_ALIASES` 的作法）。這裡因為只是布林判斷、無需追溯回正式名稱，用平坦 frozenset 沒有實質缺點，純粹是風格上可以更一致，不影響正確性，不要求修改。

### ✅ What looks good

- 白名單比對正確性是本次驗收重點，測試特別覆蓋了 `Bloomberg Law`/`via Bloomberg`/`Bloomberg News Wire` 這類 substring 陷阱且全部正確回 False，`casefold()` 完全比對實作與 docstring 描述（`tools/news.py:85-89`）一致，未發現漏洞
- SQLite 遷移完全比照既有 `earnings_calendar.updated_at` 的模式（`PRAGMA table_info` 檢查 + 條件式 `ALTER TABLE`），風格一致，且有專門測試手刻舊 schema + 舊資料驗證遷移後行為，覆蓋了「舊資料不丟、新資料正常寫入」兩個關鍵斷言
- DynamoDB 端 `source`/`high_credibility` 的處理策略考慮周到且與既有欄位一致：`source` 比照 `symbol`/`url` 的「None 不寫入 attribute」模式（避免 DynamoDB 存 `None` 值），`high_credibility` 則固定寫布林（因為它是衍生欄位、永遠有明確值，None 值沒有「省略」的語意），`list_news_since` 讀回時用 `bool(item.get(...))` 保底，兩端一致
- Big7 與 AI 兩條入庫線的改動完全對稱（`market_summary.py:145-155` vs `170-180`），沒有漏掉任一條線
- `select_news`/`_format_news_block`/`generate_market_summary` 等既有摘要邏輯程式碼零改動，符合「入庫不過濾」的需求，diff 沒有夾帶無關重構
- diff 集中在 4 個 source 檔（`tools/news.py`、`market_summary.py`、`db/backend.py`、`db/upload_local_store.py`）+ 4 個對應測試檔，無多餘改動，surgical

## 結論

- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞項目，🟢 建議項目不影響合併
