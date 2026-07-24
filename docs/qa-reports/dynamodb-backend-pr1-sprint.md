# QA Report: dynamodb-backend-pr1-sprint

- **Contract**: docs/contracts/dynamodb-backend-pr1-sprint.md
- **Date**: 2026-07-24
- **Verdict**: ⚠️ Pass with issues

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| D1 config.py — 四個 table name accessor | ✅ | `watchlist_table()` / `earnings_calendar_table()` / `earnings_notifications_table()` / `company_summaries_table()`，env + fallback 皆與 spec 一致，風格比照既有 accessor |
| D2 StorageBackend Protocol — 7 個升格方法 + docstring | ✅ | 簽章齊全；docstring 明確標註 news/market_summaries/export_rows 仍 local-only 並引用 ADR-0003/0004 |
| D3 DynamoDBBackend 補實作 | ✅ | 逐條核對：`get_watchlist`（None→DEFAULT_WATCHLIST 過渡分支已註明 PR-2 移除）、`add/remove_to_watchlist`（大寫正規化 + conditional put/delete）、`seed_watchlist_if_empty`（Limit=1 判空 + batch_writer）、`upsert_earnings_calendar`（Decimal 轉換、None 不寫、45 天 TTL）、`get_active_watchlist_symbols`（set 聯集 + 排序）、`get_watchlist_earnings_for_date`（GSI 一次查 + 逐 subscriber 交集 + 分頁處理）、`get_chat_watchlist_earnings`（BETWEEN + 排序）、`unnotified_symbols`/`mark_notified`（conditional put 冪等，僅吞 ConditionalCheckFailedException）、`get/upsert_company_summary` 皆與 local 行為對齊 |
| D4 infra/template.yaml 四張表 + env + IAM | ✅ | 4 張表 PK/SK/GSI/TTL 與 spec 逐項相符；`Globals.Environment.Variables` 補 4 個 env；ProcessorFunction 新增 WatchlistTable CRUD；SchedulerFunction 新增 4 張表 CRUD（含既有 SubscribersTable 讀取權保留）；WebhookFunction 未變 |
| D5 db/upload_local_store.py 納入 watchlist | ✅ | `export_rows()` 新增 watchlist（chat_id, position 排序）；`_write_rows` 寫入 watchlist 表；dry-run/write 計數含 `watchlist=<n>`；三個快取表確認不上傳（有測試斷言 scan count 為 0） |
| scheduler_handler.py / processor_handler.py 原始碼零改動 | ✅ | `git diff main -- src/agent/processor_handler.py src/agent/scheduler_handler.py` 輸出為空 |

## Test results

- 測試指令：`cd /Users/yehhungwei/stock-agent-worktrees/pr1-dynamodb-backend && PYTHONPATH=src uv run pytest tests/ -v`
- 結果（沙盒內）：144 passed / 0 failed / 12 errors（`test_local_integration.py` 的 `finnhub_stub` fixture 需要 bind local socket，屬沙盒網路限制，與本次改動無關）
- 結果（非沙盒重跑）：**156 passed / 0 failed**，與 generator 自述一致
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
- **tests/test_dynamodb_backend.py**：contract「Tests required」明確要求 `get_chat_watchlist_earnings` 覆蓋「日期區間過濾」，但目前三處呼叫（第 64/68/92 行）都只用寬區間 `"2026-01-01" ~ "2026-12-31"`，沒有任何測試驗證「earning_date 落在區間外的列被排除」。`DynamoDBBackend.get_chat_watchlist_earnings`（`src/agent/db/backend.py:920-952`）用 `Key("earning_date").between(date_from, date_to)`，是標準 boto3 API，實作本身風險低，但這是 contract 明確列出的驗收測試場景，目前確實缺漏（local 端 `test_storage_backend.py` 也同樣沒有這個場景，非本 PR 新增的缺口，但既然 contract 把它列在本 PR 的 Tests required，仍應補上）。
  建議修法：加一個 case，例如寫入 `earning_date` 分別為 `2026-06-01`（區間外）與 `2026-07-23`（區間內）的兩筆，`get_chat_watchlist_earnings("111", "2026-07-01", "2026-07-31")` 只應回傳區間內那筆。

### 🟢 Suggestions
- **tests/test_dynamodb_backend.py:9-32**（`test_dynamodb_earnings_cache_and_active_symbols_join`）：測試名稱暗示同時驗證 earnings cache 與 active symbols join，但實際斷言只驗證 `get_active_watchlist_symbols()`，`upsert_earnings_calendar` 呼叫在此測試中是無關的 setup noise。不影響正確性，純粹可讀性；可考慮拆開或改測試名稱。
- **src/agent/db/backend.py:800-815**（`add_to_watchlist`）：query 現有項算 `next_position` 與 conditional put 是兩次獨立呼叫，非原子操作，理論上同一 chat_id 併發新增時可能算出重複 position。這是 contract/spec 明確指定的設計（D3 第 23 行），非本次實作偏差；產品為低流量單使用者場景，可接受，僅供未來留意。

### ✅ What looks good
- `DynamoDBBackend` 與 `LocalSQLiteBackend` 的欄位映射、大寫正規化、`None` 值不寫入、float/Decimal 轉換全部對齊，且有專門的 parity 測試（`test_dynamodb_local_parity_for_watchlist_and_earnings_join`）直接把兩個 backend 用相同輸入跑一遍斷言結果相等，鎖住行為一致性，是很扎實的驗收手段。
- moto 測試確實驗證了「行為」而非只驗證「不拋錯」：per-subscriber scoping（兩個 subscriber 分別只拿到自己 watchlist 的財報列）、inactive subscriber 被排除、冪等重跑後 `unnotified_symbols` 不再回已通知 symbol 且 `mark_notified` 二次呼叫不拋例外，都有明確斷言。
- `mark_notified`/`add_to_watchlist`/`remove_from_watchlist` 全部嚴格遵守 contract「只吞 ConditionalCheckFailedException、其餘 ClientError 往上拋」的錯誤處理邊界，三處實作一致。
- `infra/template.yaml` 的 GSI、TTL、IAM 範圍與 spec 逐項核對皆正確；`DynamoDBCrudPolicy` 隱含涵蓋 `table/index/*`，GSI query 權限無需額外聲明，符合 contract 描述。
- `scheduler_handler.py` / `processor_handler.py` 原始碼零改動已用 `git diff` 實測確認，符合 Definition of Done；`test_processor_handler.py` 的更新只改測試斷言（NotImplementedError catch 變 dead code 但保留給 PR-2），並在測試中留註解說明原因，交接清楚。
- `db/upload_local_store.py` 的測試明確用 `scan()["Count"]` 斷言三個快取表在 `--write` 後仍為 0，直接驗證「不上傳」這個負向邊界，而不是只驗證 watchlist 有寫入。

## 結論
- 是否可合併：是（🟡 為測試覆蓋缺口，非功能缺陷，不阻塞合併，但建議在下一輪補上）
- 最該先處理的 1 件事：補 `get_chat_watchlist_earnings` 的日期區間過濾測試（區間外的財報列應被排除），對齊 contract 明確要求的 Tests required 項目
