# Spec: 雲端 daily pipeline 完整化（PR-A）

> 對應方向決策：`docs/decisions/0006-daily-pipeline-to-cloud.md`（ADR-0006）
> Roadmap 定位：`docs/TASKS.md` → Phase 2（重排後）PR-A。PR-B（observability + schedule ENABLE）另立 sprint。

## Goal
讓雲端 `SchedulerFunction` 能在單次 invocation 內跑完整 daily pipeline（sync earnings → 財報通知 → 每日大盤摘要），全程走 `DynamoDBBackend`，並補齊唯一缺口（news / market_summaries 的 DynamoDB 處置）。**本 PR 不改排程狀態（schedule 維持 `DISABLED`），故對 production 無行為變更。**

## User stories
- As the 系統維運者, I want 雲端 scheduler 直讀 DynamoDB 跑完整 daily pipeline, so that daily 通知不再綁單一台 macOS/launchd。
- As a 訂閱者, I want 雲端每日照樣收到「明日追蹤清單財報提醒」與「科技股大盤摘要」, so that 上雲後通知體驗與本地一致。

## Acceptance criteria
- [ ] `DynamoDBBackend` 新增 `insert_news(items) -> int`：以 `NewsTable`（PK=`headline`）put_item 寫入，headline 去重（重覆 headline 視為已存在、不計入回傳數），回傳「新寫入」筆數（語意對齊 `LocalSQLiteBackend.insert_news`：不是嘗試數）。寫入帶 `bucket="news"`、`published_at`、`ttl`（`now + NEWS_TTL_SECONDS`），與 `upload_local_store._news_item` 的欄位形狀一致。
- [ ] `DynamoDBBackend` 新增 `list_news_since(published_from) -> list[dict]`：查 `published-at-index` GSI（`bucket="news"` AND `published_at >= published_from`），newest first（對齊 local `ORDER BY published_at DESC`），處理 pagination。回傳列的 dict 形狀對齊 local（含 `id`、`category`、`symbol`、`headline`、`summary`、`url`、`published_at`），其中 `id` 值＝`headline`（使 `generate_market_summary` 無需修改即可取 `it["id"]`）。
- [ ] `DynamoDBBackend` 新增 `insert_market_summary(summary, news_ids) -> None`：no-op（雲端不持久化 market_summaries，見 ADR-0006 §3），回傳 `None`。
- [ ] 上述三方法升格進 `StorageBackend` Protocol（更新 Protocol 定義與 docstring：不再標為 local-only；`export_rows` 維持 local-only）。
- [ ] `push_daily_summary()` 與 `generate_market_summary()`／`select_news()`／`refresh_news()` 在 `STORAGE_BACKEND=dynamodb` 下可端到端跑通（讀寫走 DynamoDB），且 `market_summary.py` 本身不需修改（若需 1 行改動請於 QA report 標明理由）。
- [ ] `scheduler_handler` 移除 `_notify_live()` 與 `_run()` 的 `selected_backend_name()` 分岔；`_run()`／通知路徑對兩個 backend 都走 per-subscriber、快取化、冪等的 `_notify_local` 邏輯。
- [ ] `DynamoDBBackend.get_watchlist` 移除 `chat_id=None → DEFAULT_WATCHLIST` 過渡分支；`chat_id` 改為必填（簽章與 local 對齊）。移除因此產生的 orphan import。
- [ ] `scheduler_handler.handler()`（Lambda 入口）依序跑：`sync_earnings()` → 財報通知（明日）→ `push_daily_summary()`。各階段獨立 try/except：單一階段失敗記 log 不阻擋後續；任一階段失敗則跑完所有階段後 re-raise，使 invocation 計入 Lambda `Errors`。
- [ ] 更新 `scheduler_handler` 模組 docstring：移除「DynamoDB backend keeps the original live-fetch flow against the global DEFAULT_WATCHLIST」等已不成立的描述。
- [ ] `infra/template.yaml` 的 `SchedulerFunction` IAM 補 `NewsTable` 的 DynamoDB CRUD 權限（scheduler 現在要讀寫 news）；schedule **維持 `State: DISABLED`**。
- [ ] 本地行為不變：local launchd 仍透過既有 make targets（`sync-earnings` / `notify-earnings` / `daily-summary`）分步執行；`__main__` CLI 的 `sync` 參數與預設 notify 行為不改。

## Non-goals
- 不改排程狀態（不 ENABLE、不改 cron／時區）——屬 PR-B。
- 不建 SNS / CloudWatch Alarm / email 參數——屬 PR-B。
- 不建 `market_summaries` 的 DynamoDB 表（雲端刻意不持久化）。
- 不 `make deploy`、不做部署後雲端驗證——屬 Phase 3（infra-engineer / 使用者）。
- 不停用本地 launchd、不動 downsync 模組——屬 Phase 3 ops。
- 不改 webhook / processor 行為。
- 不改 `earnings_calendar` / `company_summaries` / `earnings_notifications` 的既有 DynamoDB 實作（PR-1 已完成）。

## Open questions
- 無阻擋性問題。快取策略（news 落庫、market_summaries no-op）與死碼處置已於 ADR-0006 定案。排程確切時間（建議沿用 10:30 台北）於 PR-B 定案、非本 PR 範圍。
