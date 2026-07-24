# Contract: 雲端 daily pipeline 完整化（PR-A）

對應 spec: docs/specs/cloud-daily-pipeline.md
方向決策: docs/decisions/0006-daily-pipeline-to-cloud.md（ADR-0006）

> 名詞對照（見 CLAUDE.md）：應用程式碼在 `src/agent/`；測試在 `tests/`；測試指令 `PYTHONPATH=src uv run pytest tests/ -v`。DynamoDB 測試用 `moto`；Groq / Finnhub / Telegram 用 `pytest-mock` / monkeypatch。fixtures 見 `tests/conftest.py`（已自動建表建 queue）。

## Deliverables

### D1. DynamoDBBackend 補 news / market_summaries 方法
- [ ] `insert_news(self, items: list[dict]) -> int`：寫 `NewsTable`（`self._news`，於 `__init__` 建 table resource，table 名取自 `config.news_table()`）。
  - 以 `headline` 為 PK，`put_item` 去重。回傳「新寫入」筆數，語意對齊 local（重覆 headline 不計入）。實作提示：headline 已存在時 put 會覆蓋，需先判斷是否已存在（例如 `attribute_not_exists(headline)` conditional put + 吞 `ConditionalCheckFailedException` 累計成功數），以正確計數 distinct inserted。
  - Item 欄位形狀對齊 `db/upload_local_store.py:_news_item`：`headline`、`bucket="news"`、`published_at`、`category`、`summary`、`fetched_at`、`ttl = int(time.time()) + NEWS_TTL_SECONDS`；`symbol` / `url` 為 `None` 時不寫該屬性。
- [ ] `list_news_since(self, published_from: str) -> list[dict]`：查 `published-at-index` GSI，`KeyConditionExpression = Key("bucket").eq("news") & Key("published_at").gte(published_from)`，`ScanIndexForward=False`（newest first），跟進 `LastEvaluatedKey` 分頁。
  - 回傳每列 dict 形狀對齊 local `list_news_since`：`{"id": <headline>, "category", "symbol", "headline", "summary", "url", "published_at"}`。`id` 值＝`headline`（讓 `generate_market_summary` 的 `it["id"]` 無需改）。缺漏的 `symbol` / `url` 回 `None`。
- [ ] `insert_market_summary(self, summary: str, news_ids: list) -> None`：no-op，回 `None`（雲端不持久化 market_summaries，ADR-0006 §3）。

### D2. Protocol 升格
- [ ] 將 `insert_news` / `list_news_since` / `insert_market_summary` 加入 `StorageBackend` Protocol（`db/backend.py`）。
- [ ] 更新 Protocol docstring：這三個方法已成跨 backend 共用（延伸 ADR-0004 §7、依 ADR-0006 §4），不再標為 local-only；`export_rows` 仍 local-only。

### D3. scheduler_handler 死碼移除 + pipeline 組裝
- [ ] 移除 `_notify_live()`（`scheduler_handler.py`）。
- [ ] `_run()` 移除 `selected_backend_name()` 分岔，統一走 `_notify_local(tomorrow)`（兩個 backend 皆適用）。移除因此不再需要的 import（`get_watchlist`、`get_all_active`、`selected_backend_name`、`PROMPT_TEMPLATE` 等——僅移除 YOUR 改動造成的 orphan，逐一確認）。
- [ ] `handler(event, context)`：單次 invocation 依序執行三階段：`sync_earnings()` → `_notify_local(tomorrow)` → `push_daily_summary()`（`from agent.market_summary import push_daily_summary`）。
  - 每階段獨立 try/except，失敗以 `logger.exception` 記錄且不阻擋後續階段。
  - 三階段跑完後，若任一階段曾拋例外，`raise`（可包成單一例外或 re-raise 首個）使 invocation 計入 Lambda `Errors`。全部成功回 `{"statusCode": 200}`。
- [ ] 更新模組 docstring：移除「DynamoDB backend keeps the original live-fetch flow against the global DEFAULT_WATCHLIST」等過時描述，改述雲端 handler 跑完整 pipeline。

### D4. get_watchlist 過渡分支移除
- [ ] `DynamoDBBackend.get_watchlist`：移除 `chat_id is None → DEFAULT_WATCHLIST` 分支，`chat_id` 改必填（簽章對齊 `LocalSQLiteBackend.get_watchlist(self, chat_id)`）。更新 Protocol 對應簽章（`get_watchlist(self, chat_id: str)`）。移除 orphan import（`DEFAULT_WATCHLIST`）。

### D5. IAM
- [ ] `infra/template.yaml` `SchedulerFunction` `Policies` 補 `NewsTable` 的 CRUD：
  ```yaml
  - DynamoDBCrudPolicy:
      TableName: !Ref NewsTable
  ```
- [ ] `SchedulerFunction` 的 `Daily` event **維持 `State: DISABLED`**（本 PR 不 ENABLE）。

## Behaviors（輸入 → 輸出）
- [ ] `insert_news`：對 `NewsTable` 送兩批含重覆 headline 的 items → 第一批全計入回傳數，第二批重覆的不計入（回 0 或僅新 headline 數）；表內每 headline 僅一列。
- [ ] `list_news_since`：published_at 早於 `published_from` 的列不回；回傳 newest first；跨分頁完整。
- [ ] `insert_market_summary`（DynamoDB）：呼叫後回 `None`，不寫任何表、不拋例外。
- [ ] `handler`：sync/notify/summary 全成功 → 回 `{"statusCode": 200}`，且無例外。
- [ ] `handler`：其中一階段拋例外 → 其餘階段仍執行（副作用保留），最終 handler `raise`（invocation 失敗）。

## Out of scope（本 sprint 不做）
- 排程 ENABLE / cron / 時區調整、SNS / CloudWatch Alarm / email 參數 → PR-B。
- `market_summaries` DynamoDB 表。
- `make deploy`、部署後雲端驗證、停本地 launchd、downsync 停用 → Phase 3 ops。
- 改 webhook / processor / 既有 earnings_calendar・company_summaries・earnings_notifications DynamoDB 實作。

## Tests required
以 moto 覆蓋新 DynamoDB backend 方法與 handler 路徑（DynamoDB 模式）。新 tests 建議放 `tests/test_storage_backend.py`（backend 方法）與 `tests/test_scheduler_handler.py` 或既有 scheduler 測試檔（handler pipeline）。fixtures 需能在 dynamodb 模式建 `NewsTable`（若 `tests/conftest.py` 尚未建 news 表，補上，形狀對齊 template：PK=headline、GSI `published-at-index`(bucket HASH, published_at RANGE)、TTL）。

- [ ] `insert_news`（DynamoDB）：新 headlines 回正確 distinct 筆數；重覆 headline 再送回 0 且表列數不變（對齊 local `test_insert_news_*` 兩案的語意）。
- [ ] `list_news_since`（DynamoDB）：只回 `published_at >= from` 的列；newest first；回列含 `id==headline` 與 local 形狀欄位；（可選）多列跨分頁。
- [ ] `insert_market_summary`（DynamoDB）：回 `None`、不拋、不建任何表列。
- [ ] `push_daily_summary` 於 dynamodb backend 端到端（mock Finnhub company/tech news + index quotes、mock Groq client、mock `send_message`）：news 寫入 DynamoDB → 讀回 → 生成摘要 → 推播 active 訂閱者；`market_summary.py` 未被修改（或修改僅 1 行且 QA report 說明）。
- [ ] `handler`（dynamodb 模式，mock 外部呼叫）：三階段皆被呼叫、全成功回 200。
- [ ] `handler` 容錯：令某一階段（如 summary）拋例外 → 斷言其餘階段仍執行、handler 最終 raise。
- [ ] 回歸：`_notify_live` 已移除（import / 呼叫不存在）；`get_watchlist(None)` 分支移除後，對舊行為的依賴不再（若原有測試依賴 `get_watchlist()` 無參回 DEFAULT_WATCHLIST，需更新或移除並於 QA report 標明）。
- [ ] 既有全測試綠：`PYTHONPATH=src uv run pytest tests/ -v`。

## Definition of done
- 全部 deliverable 打勾。
- 專案測試指令（`PYTHONPATH=src uv run pytest tests/ -v`）全綠。
- evaluator 的 QA report 無 P0/P1。
- 確認未改排程狀態（schedule 仍 `DISABLED`）、未新增 SNS/alarm（那是 PR-B）、未 deploy。
