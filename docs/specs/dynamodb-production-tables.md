# Spec: DynamoDB production tables + DynamoDBBackend 補實作（Phase 2 PR-1）

## Goal
在 DynamoDB 建立 watchlist / earnings 快取 / 通知冪等 / 公司摘要四張新表，並讓 `DynamoDBBackend` 補齊對應方法（行為對齊 `LocalSQLiteBackend`），使 production 具備與 local 相同的「存 DB → 讀 DB、per-subscriber、冪等」資料能力，為 PR-2（scheduler 遷移）與 PR-2 的 processor `/watch`/`/unwatch` 鋪路。

## 背景與範圍界定
- 本 PR 是 `docs/TASKS.md` Phase 2 的 **PR-1**，只做「表 + backend 方法 + infra + 測試」。
- **不改** `scheduler_handler.py` 邏輯（`_notify_live` 移除、sync/notify 拆分走 DynamoDB 是 PR-2）。
- **不改** `processor_handler.py`（`/watch`/`/unwatch` 的 `LOCAL_ONLY_MESSAGE` 死碼移除是 PR-2）。
- **不含** news / market_summaries 相關方法與表（`insert_news` / `list_news_since` / `insert_market_summary`），那是 PR-3。
- 設計方向已由使用者定案（多表風格、application-side join、conditional-put 冪等），本 spec 只做細化，不重開需求討論。決策細節見 ADR-0004。

## User stories
- As a maintainer, I want the four production tables defined in SAM, so that a single `make deploy` provisions everything Phase 2 needs.
- As a maintainer, I want `DynamoDBBackend` to implement the same watchlist/earnings/notification/summary methods as `LocalSQLiteBackend`, so that PR-2 can run the exact sync/notify flow on Lambda without backend-specific branches.
- As a maintainer, I want notification idempotency backed by conditional put, so that a re-run of the daily job never double-notifies a subscriber.
- As a maintainer, I want my locally curated watchlist to survive the cloud cutover, so that production notifications work immediately after deploy without re-issuing `/watch`.
- As a maintainer, I want moto tests covering every new backend method, so that behaviour parity with local is locked before deploy.

## 表結構（DynamoDB，多表風格，PAY_PER_REQUEST）

命名沿用既有 `${AppName}-<kebab>` 慣例（`AppName` 預設 `earnings-agent`）。所有表 `BillingMode: PAY_PER_REQUEST`（on-demand）。

### 1. watchlist — `${AppName}-watchlist`
| 屬性 | 型別 | 角色 |
|------|------|------|
| `chat_id` | S | PK (HASH) |
| `symbol` | S | SK (RANGE) |
| `position` | N | item attr（排序用；讀取時 Python 端 `sorted()`，不靠 SK） |

- **TTL**：無（使用者資料，永久保存）。
- **GSI**：無。反向查詢（哪些 subscriber 追蹤某 symbol）在本 PR 選定的 join 策略下用不到（見下方 join 策略與 ADR-0004「symbol GSI 省略」），故 spec 草案 `docs/specs/per-subscriber-watchlist.md:65-73` 提的 symbol GSI **不建**。
- 對應 local `watchlist(chat_id, symbol, position)` 表。

### 2. earnings_calendar — `${AppName}-earnings-calendar`
| 屬性 | 型別 | 角色 |
|------|------|------|
| `symbol` | S | PK (HASH) |
| `earning_date` | S | SK (RANGE)，ISO 日期字串 `YYYY-MM-DD` |
| `extended_hour` | S (nullable) | 盤前/盤後標記，可缺 |
| `eps_estimate` | N (nullable) | 分析師 EPS 預期，可缺 |
| `revenue_estimate` | N (nullable) | 分析師營收預期，可缺 |
| `updated_at` | S | 最後同步時間（ISO，UTC），供 stale 判斷 |
| `ttl` | N | TTL 屬性 |

- **GSI**：`earning-date-index`，PK=`earning_date`（S），Projection **ALL**（`get_watchlist_earnings_for_date` 需讀回 extended_hour / eps_estimate / revenue_estimate / updated_at / symbol）。供「查某日全部財報」。
- **TTL**：啟用於 `ttl`。策略：`upsert_earnings_calendar` 寫入時 `ttl = now + 45 天`（epoch 秒）。理由：sync 只抓未來 `SYNC_DAYS = 30` 天，仍相關的未來財報每次 sync 都被 `put_item` 覆寫、TTL 一併刷新；財報日過後不再被 sync 的舊列約 45 天後自動清除。45 = 30 天 horizon + 15 天緩衝，避免剛過期的資料太快消失影響除錯。
- **數值型別**：boto3 DynamoDB resource 不接受 Python `float`，寫入時 `eps_estimate` / `revenue_estimate` 需轉 `Decimal(str(value))`；**讀取時轉回 `float`**，以與 `LocalSQLiteBackend`（回傳 REAL/float）的回傳 shape 對齊。`None` 值不寫入該屬性（保持 nullable）。
- 對應 local `earnings_calendar(symbol, earning_date, extended_hour, eps_estimate, revenue_estimate, updated_at)` 表。

### 3. earnings_notifications — `${AppName}-earnings-notifications`
| 屬性 | 型別 | 角色 |
|------|------|------|
| `chat_id` | S | PK (HASH) |
| `notify_key` | S | SK (RANGE)，格式 `"{symbol}#{earning_date}"` |
| `symbol` | S | item attr（除錯用） |
| `earning_date` | S | item attr（除錯用） |
| `sent_at` | S | 推播時間（ISO，UTC） |
| `ttl` | N | TTL 屬性 |

- **TTL**：啟用於 `ttl`。策略：`mark_notified` 寫入時 `ttl = now + 30 天`。理由：財報日過後該去重標記即無用；30 天足以涵蓋單日通知窗口並容忍時區/重跑。
- **GSI**：無。
- **冪等**：改用 conditional put（`attribute_not_exists(notify_key)`），同現有 `claim_update_id` pattern（見 ADR-0004）。
- 對應 local `earnings_notifications(chat_id, symbol, earning_date, sent_at)`，PK `(chat_id, symbol, earning_date)`；DynamoDB 以複合 SK `symbol#earning_date` 表達同一唯一性。

### 4. company_summaries — `${AppName}-company-summaries`
| 屬性 | 型別 | 角色 |
|------|------|------|
| `symbol` | S | PK (HASH) |
| `earning_date` | S | SK (RANGE) |
| `summary` | S | 快取的 Groq 摘要文字 |
| `created_at` | S | 產生時間（ISO，UTC） |
| `ttl` | N | TTL 屬性 |

- **TTL**：啟用於 `ttl`。策略：`upsert_company_summary` 寫入時 `ttl = now + 30 天`。理由：摘要綁定某次財報日，過後即失效可清除。
- **GSI**：無。
- 對應 local `company_summaries(symbol, earning_date, summary, created_at)`。

## `DynamoDBBackend` 方法補實作

以下方法目前在 `DynamoDBBackend` 為 no-op / `NotImplementedError` / degenerate（回 `DEFAULT_WATCHLIST`），本 PR 補為真實實作，**行為需與 `LocalSQLiteBackend` 對齊**（回傳 key、大小寫正規化、回傳型別）。

### 既有於 Protocol、需改為真實實作
- `get_watchlist(chat_id=None)`：
  - `chat_id` 非 None → query watchlist 表 PK=chat_id，回 `[{"symbol": ...}]`，依 `position` 在 Python 端 `sorted()`。對齊 local。
  - `chat_id is None` → **維持回傳 `DEFAULT_WATCHLIST` copy**（過渡分支，保留 `_notify_live` 現狀不變；PR-2 移除 `_notify_live` 後可一併移除此分支）。
- `add_to_watchlist(chat_id, symbol)`：`symbol.upper()`；先 query 該 chat_id 現有項判斷是否已存在、並算出 `next_position = max(position)+1`（空清單為 0）；以 conditional put（`attribute_not_exists(symbol)`）寫入。已存在回 `False`，新增成功回 `True`。對齊 local 語意。
- `remove_from_watchlist(chat_id, symbol)`：`symbol.upper()`；`delete_item` 帶 `ConditionExpression=attribute_exists(symbol)`；成功回 `True`，`ConditionalCheckFailedException` 回 `False`。對齊 local `rowcount > 0`。
- `seed_watchlist_if_empty(chat_id)`：query 該 chat_id（`Limit=1`）判斷是否已有列；若空則以 `DEFAULT_WATCHLIST` `enumerate` 出 position 用 `batch_writer` 寫入。對齊 local。
- `upsert_earnings_calendar(items)`：對每個含 `symbol` 與 `date` 的 item，`put_item` 寫入 earnings_calendar（`symbol.upper()`、`earning_date=item["date"]`、`extended_hour=item.get("hour")`、`eps_estimate`/`revenue_estimate` 轉 Decimal、`updated_at=now`、`ttl=now+45天`）。空 items 直接 return。對齊 local 欄位映射。

### 由 local-only「升格」進 Protocol、需在兩個 backend 都實作
（本 PR 將這些方法加入 `StorageBackend` Protocol——它們現在是跨 backend 共用，見 ADR-0004 對 ADR-0003 的範圍延伸）
- `get_active_watchlist_symbols()`：`get_all_active()` → 逐 chat_id query watchlist → 以 Python `set` 聯集 symbol → 回 `[{"symbol": ...}]`，依 symbol 排序。對齊 local 的 `SELECT DISTINCT`。
- `get_watchlist_earnings_for_date(earning_date)`：以 `earning-date-index` GSI query 當日全部 earnings（一次）→ 建 symbol→earnings 資料 map；`get_all_active()` → 逐 active subscriber query watchlist → 對「watchlist symbol ∩ 當日 earnings」的每個 symbol 產出一列 `{chat_id, symbol, earning_date, extended_hour, eps_estimate, revenue_estimate, updated_at}`。輸出依 `chat_id` 再 `position` 排序。對齊 local 的 `earnings ⋈ watchlist ⋈ subscribers(active=1)`。
- `get_chat_watchlist_earnings(chat_id, date_from, date_to)`：query 該 chat_id watchlist → 對每個 symbol query earnings_calendar `SK BETWEEN date_from AND date_to`（或 BatchGetItem）→ 回 `[{symbol, earning_date, extended_hour, eps_estimate, revenue_estimate, updated_at}]`，依 `earning_date` 再 `position` 排序。對齊 local。
- `unnotified_symbols(chat_id, symbols, earning_date)`：`symbols` 為空回 `[]`；對每個 symbol 組 `notify_key = f"{symbol}#{earning_date}"` 檢查 earnings_notifications 是否已有該 key（BatchGetItem 或逐 key get_item）；回未通知過的 symbols（保持傳入順序）。對齊 local。
- `mark_notified(chat_id, symbols, earning_date)`：對每個 symbol 以 conditional put（`attribute_not_exists(notify_key)`）寫入，`sent_at=now`、`ttl=now+30天`；`ConditionalCheckFailedException` 視為已標記、吞掉不報錯（對齊 local `INSERT OR IGNORE` 的冪等語意）。
- `get_company_summary(symbol, earning_date)`：`get_item` PK=symbol SK=earning_date，回 `summary` 或 `None`。對齊 local。
- `upsert_company_summary(symbol, earning_date, summary)`：`put_item`，`created_at=now`、`ttl=now+30天`。對齊 local。

### 明確不在本 PR 實作（維持 local-only、不進 Protocol）
`insert_news` / `list_news_since` / `insert_market_summary`（PR-3）、`export_rows`（local-only，供 upload 工具用）。

## 設定與基礎設施

### config.py
新增四個 table name accessor，沿用既有 env-fallback 風格：
- `watchlist_table()` → env `WATCHLIST_TABLE`，fallback `"earnings-agent-watchlist"`
- `earnings_calendar_table()` → env `EARNINGS_CALENDAR_TABLE`，fallback `"earnings-agent-earnings-calendar"`
- `earnings_notifications_table()` → env `EARNINGS_NOTIFICATIONS_TABLE`，fallback `"earnings-agent-earnings-notifications"`
- `company_summaries_table()` → env `COMPANY_SUMMARIES_TABLE`，fallback `"earnings-agent-company-summaries"`

### infra/template.yaml
- 新增四張表資源（見上方表結構；earnings_calendar 含 `earning-date-index` GSI 與 TTL；notifications / company_summaries 含 TTL；watchlist 無 TTL/GSI）。
- `Globals.Function.Environment.Variables` 新增四個 table name env（`!Ref` 對應表），沿用既有四張表的 env 注入模式。
- IAM 調整（本 PR 一次補齊，PR-2 不再動 infra）：
  - **ProcessorFunction**：新增 `DynamoDBCrudPolicy` on watchlist（PR-2 的 `/watch`/`/unwatch`/`/watchlist`/`/subscribe` seed 需要）。
  - **SchedulerFunction**：新增 `DynamoDBCrudPolicy` on watchlist、earnings_calendar、earnings_notifications、company_summaries（PR-2 sync/notify 走 DynamoDB 需要；`DynamoDBCrudPolicy` 已涵蓋 `table` 與 `table/index/*`，GSI query 可用）。既有 subscribers 讀取權限保留。
  - **WebhookFunction**：不變。

### db/upload_local_store.py
- **決策：只新增 `watchlist` 到上傳範圍**；earnings_calendar / earnings_notifications / company_summaries **不上傳**。
- 理由：
  - watchlist 是真實使用者資料，cutover 後 production 需立即擁有，否則 scheduler 找不到任何 subscriber 的追蹤 symbol；靠使用者重跑 `/subscribe`（只會 seed DEFAULT）或逐檔 `/watch` 補回不可靠。故納入上傳。
  - earnings_calendar / company_summaries 是暫存快取，production 首次 sync cron 會重建，上傳陳舊資料無益。
  - earnings_notifications 是去重標記；不上傳的唯一風險是 cutover 當天可能對「local 已推、cloud 再推一次」的隔日財報重複通知一次——單一使用者、單日、一次性邊界，可接受；Phase 3 cutover 步驟可安排在 local notify 窗口後切換以規避。
- 具體改動：`LocalSQLiteBackend.export_rows()` 增加 `"watchlist"`（`chat_id, symbol, position`）；`_write_rows` 寫入 watchlist 表；dry-run 計數行加入 `watchlist=<n>`。

## Acceptance criteria
- [ ] `infra/template.yaml` 定義四張新表：watchlist（PK chat_id / SK symbol、無 TTL/GSI）、earnings_calendar（PK symbol / SK earning_date、`earning-date-index` GSI PK=earning_date Projection ALL、TTL on `ttl`）、earnings_notifications（PK chat_id / SK notify_key、TTL）、company_summaries（PK symbol / SK earning_date、TTL），全部 `PAY_PER_REQUEST`。
- [ ] 四個 table name env 注入 `Globals`；`config.py` 有對應四個 accessor 與 env fallback。
- [ ] ProcessorFunction 具 watchlist CRUD 權限；SchedulerFunction 具 watchlist / earnings_calendar / earnings_notifications / company_summaries CRUD 權限（含 GSI）。
- [ ] `DynamoDBBackend` 不再有 no-op / `NotImplementedError` / degenerate stub（`get_watchlist(None)` 的 `DEFAULT_WATCHLIST` 過渡分支除外，並註明 PR-2 移除）。
- [ ] 上述每個 backend 方法在 moto 下行為與 `LocalSQLiteBackend` 對齊：回傳 key、`symbol` 大寫正規化、`add/remove` 的 True/False 語意、`unnotified_symbols` 保序、`mark_notified` 重跑不報錯不重複、數值欄位讀回為 `float`。
- [ ] `mark_notified` + `unnotified_symbols` 對同一 `(chat_id, symbol, earning_date)` 重跑冪等：第二次 `unnotified_symbols` 不再回該 symbol；`mark_notified` 第二次不拋例外。
- [ ] `get_watchlist_earnings_for_date` 只回「active subscriber 的 watchlist ∩ 當日 earnings」的列，且每列 scoped 到正確 `chat_id`；兩個追蹤不同 symbol 的 subscriber 各自只拿到自己的列。
- [ ] `get_watchlist(None)` 仍回 `DEFAULT_WATCHLIST`（`_notify_live` 不變）；`scheduler_handler.py` 與 `processor_handler.py` 原始碼未被本 PR 改動。
- [ ] `db/upload_local_store.py` 上傳含 watchlist、不含三個快取表；dry-run 計數含 watchlist。
- [ ] `tests/conftest.py` 的 `aws` fixture 建出四張新表（含 GSI）；既有 `test_add_remove_raise_not_implemented_for_dynamodb` 更新為驗證 dynamodb 下 add/remove 真實運作。
- [ ] `make test` 全綠；不呼叫真 AWS / Groq / Finnhub / Telegram。

## Non-goals
- 不改 `scheduler_handler.py`（`_notify_live` 移除、sync/notify 走 DynamoDB → PR-2）。
- 不改 `processor_handler.py`（`LOCAL_ONLY_MESSAGE` 死碼移除 → PR-2）。
- 不實作 news / market_summaries 的 DynamoDB 儲存（`insert_news` / `list_news_since` / `insert_market_summary` → PR-3）。
- 不建 watchlist 的 symbol GSI（本 PR join 策略用不到）。
- 不動 conversations / subscribers / processed-updates 三張既有表。
- 不做真實部署（Phase 3）。

## Open questions
- （已自行決定）watchlist symbol GSI 省略——採「逐 subscriber query watchlist + 記憶體交集」的 application-side join，訂閱者數小，反查 GSI 非必要。若日後訂閱者規模大到逐 subscriber query 成本過高，再補 symbol GSI 做反查。
- （已自行決定）earnings_notifications 不上傳，接受 cutover 當天單日可能一次重複通知的邊界；若使用者在意，Phase 3 排在 local notify 窗口後切換即可規避。
