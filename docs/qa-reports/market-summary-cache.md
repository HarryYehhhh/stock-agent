# QA Report: market-summary-cache

- **Contract**: 無正式 contract（feature skill，需求由主 Claude 於任務描述中給出）
- **Date**: 2026-07-26
- **Verdict**: ✅ Pass

## 需求 checklist
| 項目 | Status | Note |
|---|---|---|
| 1. `generate_market_summary` cache key `(summary_date, days_back, news_ids_hash)`，命中不打 Groq、log 標明 hit，回傳結構相容 | ✅ | `market_summary.py:251-267`；`get_market_summary` tool 只讀 `summary`/`date_range`（皆在），`push_daily_summary` 讀 `summary`/`news`（皆在）皆相容 |
| 1a. 無新聞 early-return 不快取 | ✅ | early return（`market_summary.py:243-249`）在 cache 查詢之前，不觸碰 cache |
| 1b. 命中時指數為先前快照（accepted trade-off） | ✅ | cache hit 回傳 `indexes=[]`，`summary` 文字本身內嵌前次 index_block；未重打 `fetch_index_quotes()` |
| 2. SQLite 新增三欄 + ALTER TABLE migration + `insert_market_summary`/`get_cached_market_summary`/Protocol 同步 | ✅ | `backend.py:261-281`（migration 用 PRAGMA 偵測避免重複 ALTER）、`692-737`；Protocol `123-134` |
| 3. DynamoDB `insert_market_summary` no-op → 寫入既有 NewsTable、~2 天 TTL；新增 `get_cached_market_summary`；不開新表/不動 infra | ✅ | `backend.py:1225-1263`；沿用 `bucket`/`headline` PK，複用既有 GSI 設計慣例（與 `insert_news` 一致）；`infra/template.yaml` NewsTable 未變動、TTL 早已 `Enabled: true` |
| 4. ADR-0006 §3 追加修訂註記 | ✅ | `docs/decisions/0006-daily-pipeline-to-cloud.md` 新增一段「2026-07-26 修訂」，未重寫原文 |
| 5a. 同天同條件第二次不打 Groq（SQLite + DynamoDB） | ✅ | `test_generate_summary_cache_hit_skips_groq_and_reuses_summary`、`test_generate_summary_cache_hit_skips_groq_on_dynamodb_backend` |
| 5b. news 集合變 → 重打 | ✅ | `test_generate_summary_cache_miss_when_news_set_changes` |
| 5c. days_back 不同不共用 | ✅ | `test_generate_summary_cache_not_shared_across_days_back` |
| 5d. SQLite 舊 schema migration | ✅ | `test_market_summaries_schema_migration_of_pre_existing_store` |
| 5e. DynamoDB TTL 屬性正確 | ✅ | `test_dynamodb_insert_market_summary_persists_cache_entry_with_ttl` |

## Test results
- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- Sandbox 內結果：299 passed / 0 failed / 12 error（`tests/test_local_integration.py` 內需要 bind `127.0.0.1` socket 的測試，被本評估環境的 sandbox 擋下 `PermissionError: [Errno 1] Operation not permitted`，與本次改動無關）
- Sandbox 外重跑 `tests/test_local_integration.py`：26 passed / 0 failed（確認上述 12 個確實是 sandbox 限制，非改動造成的 regression）
- 全量合計：311 passed / 0 failed，與 generator 回報一致
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- `market_summary.py:252` `news_ids = [it["id"] for it in selected_news]` 用 `selected_news`（big7+ai 串接後的順序）算 hash 的輸入來源，但 `_news_ids_hash()` 內部用 `sorted(str(news_id) ...)` 做排序正規化，因此即使上游回傳順序不穩定（例如 Finnhub 回應順序有變動）也不影響 cache key 穩定性。這個穩定性處理是必要且對的，只是想指出：若未來 `_news_ids_hash` 的排序邏輯被移除或改動，需注意選新聞來源（big7/ai）目前並非保證有序。目前實作沒有問題，僅供未來修改時留意。
- DynamoDB `get_cached_market_summary` 沒有額外檢查 `item["ttl"]` 是否已過期（DynamoDB TTL 刪除有延遲，最長可能落後數小時到一天）。經確認這不構成正確性問題：cache key 已含 `summary_date`，過期未被物理刪除的 item 只會匹配「同一天」的查詢，不會造成跨日誤命中；且 push_daily_summary 只在當天呼叫一次 cron + 使用者互動觸發同天多次呼叫，2 天 TTL 遠大於單日快取需求，設計上是安全的。無需修改，記錄於此供之後檢視 NewsTable 資料量時參考。

### ✅ What looks good
- Cache lookup 精準卡在「已知有新聞」分支之後、Groq 呼叫之前，且 early-return（無新聞）路徑完全不觸碰 cache，符合需求的明確排除。
- Cache hit 時避免重複呼叫 `fetch_index_quotes()`（不只省 Groq，連 Finnhub quote 呼叫都省了），是額外的正向效果且未偏離需求範圍。
- `_news_ids_hash()` 用排序後字串 join 再 sha256，對 int/str id 型別與順序都穩定，兩個 backend（sqlite int id、DynamoDB headline string id）共用同一支函式没有分支特判。
- SQLite migration 用 `PRAGMA table_info` 判斷欄位存在再 `ALTER TABLE`，冪等、可重複執行不出錯；且 `_init_schema()` 只在 `LocalSQLiteBackend.__init__` 執行一次（backend 本身透過 `get_backend()` 的 `@lru_cache` 單例快取），不是每次查詢都重跑 migration，無效能疑慮。
- DynamoDB 快取沿用既有 `NewsTable`（`bucket="market_summary"` 區隔於 `bucket="news"`），與既有 `insert_news`/`list_news_since` 的 PK/GSI 使用模式一致，未新增表、未動 infra，直接符合「NewsTable TTL 已啟用則不動 infra」的要求。
- ADR-0006 修訂用附加段落方式呈現，保留原決策脈絡與日期，方便追溯前後決策差異。
- 呼叫端相容性覆核：`get_market_summary` tool 只讀 `summary`/`date_range`；`push_daily_summary` 只讀 `news`（判斷有無新聞用）與 `summary`（推播內容），cache hit 分支兩者皆完整提供，行為不變。
- Diff 為外科手術式改動：`market_summary.py`、`backend.py`、`market_summary_tool.py`、ADR 文件、兩個測試檔，無不相關重構或格式調整，改動皆可追溯回需求條目。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻塞事項；上述兩點 🟢 suggestion 僅供未來維護參考，不需在本次處理。
