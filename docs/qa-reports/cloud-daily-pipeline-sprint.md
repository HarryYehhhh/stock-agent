# QA Report: cloud-daily-pipeline-sprint（PR-A）

- **Contract**: docs/contracts/cloud-daily-pipeline-sprint.md
- **Date**: 2026-07-24
- **Verdict**: ✅ Pass

## Contract checklist

| Deliverable | Status | Note |
|---|---|---|
| D1. `insert_news(items) -> int`：conditional put 去重、item 形狀對齊 `_news_item` | ✅ | `backend.py:1046-1081`；`headline`/`bucket="news"`/`published_at`/`category`/`summary`/`fetched_at`/`ttl` 齊全，`symbol`/`url` 為 `None` 時不寫入該屬性，與 `upload_local_store._news_item` 逐欄位比對一致；batch 內重覆（`seen` set）與已存在（`ConditionalCheckFailedException` 吞例外）皆不計入回傳數 |
| D1. `list_news_since(published_from) -> list[dict]`：GSI 查詢、newest first、跨分頁、`id=headline` | ✅ | `backend.py:1083-1117`；`Key("bucket").eq("news") & Key("published_at").gte(...)`、`ScanIndexForward=False`、`while "LastEvaluatedKey"` 分頁、回傳形狀含 `id/category/symbol/headline/summary/url/published_at` 且 `id==headline` |
| D1. `insert_market_summary(summary, news_ids) -> None`：no-op | ✅ | `backend.py:1119-1124`，僅回 `None`，無任何寫入 |
| D2. 三方法升格進 `StorageBackend` Protocol + docstring 更新 | ✅ | `backend.py:54-116`；docstring 明確標註三方法已跨 backend 共用、`export_rows` 仍 local-only |
| D3. 移除 `_notify_live()` | ✅ | `scheduler_handler.py` diff 確認整段刪除，回歸測試 `test_notify_live_removed` 斷言 `hasattr` 為 False |
| D3. `_run()` 移除 `selected_backend_name()` 分岔，統一走 `_notify_local` | ✅ | `scheduler_handler.py:155-157`；`selected_backend_name`/`get_watchlist`/`get_all_active`/`PROMPT_TEMPLATE` import 與定義皆已移除且無 orphan（逐一核對其餘 import 仍被使用） |
| D3. `handler()` 依序 sync→notify→summary，各自 try/except，任一失敗最終 raise，全成功回 200 | ✅ | `scheduler_handler.py:160-191`；`test_handler_runs_all_three_stages_and_returns_200`、`test_handler_stage_failure_does_not_block_other_stages_but_raises` 覆蓋兩種路徑 |
| D3. 模組 docstring 更新（移除過時的 DEFAULT_WATCHLIST live-fetch 描述） | ✅ | `scheduler_handler.py:1-19` |
| D4. `DynamoDBBackend.get_watchlist` 移除 `chat_id=None` 分支，簽章對齊 local，移除 orphan import | ✅ | `backend.py:830-834`；Protocol 簽章同步改為 `get_watchlist(self, chat_id: str)`；`get_watchlist(None)` 舊測試 `test_get_watchlist_uses_code_defaults_for_dynamodb` 已隨分支移除一併刪除 |
| D5. `SchedulerFunction` IAM 補 `NewsTable` CRUD | ✅ | `infra/template.yaml:298-299` |
| D5. `Daily` event 維持 `State: DISABLED` | ✅ | `infra/template.yaml:313`，逐字確認未被改動 |

## Behaviors 核對
- `insert_news` 兩批含重覆 headline：第一批計入 distinct 數、第二批重送回 0、表內每 headline 僅一列 — ✅（`test_dynamodb_insert_news_returns_distinct_inserted_count_not_attempted_count`、`test_dynamodb_insert_news_reinserting_existing_headlines_returns_zero`）
- `list_news_since`：早於 `published_from` 排除、newest first、跨分頁 — ✅（`test_dynamodb_list_news_since_filters_orders_and_shapes_rows`；分頁為 contract 標記「可選」，未測，不扣分）
- `insert_market_summary`：回 `None`、不寫入 — ✅（`test_dynamodb_insert_market_summary_is_a_noop`）
- `handler` 全成功回 200 / 部分失敗最終 raise 且其餘階段仍執行 — ✅（見上）

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：191 passed / 12 errors（`tests/test_local_integration.py` 既有 `finnhub_stub` socket bind 沙盒限制，與本次改動無關）
- 非沙盒重跑（`dangerouslyDisableSandbox: true`）：**203 passed, 0 failed**（與 generator 自報一致）
- 失敗測試：無
- 既有 suite regression：✅

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- **`tests/test_scheduler.py`（`test_handler_stage_failure_does_not_block_other_stages_but_raises`）**：用 `pytest.raises(Exception)` 而非 `pytest.raises(RuntimeError)`。功能上不影響驗收（`handler` 目前確實 raise `RuntimeError`），但斷言型別稍寬鬆，之後若 `handler` 的例外型別意外改變不會被這個測試抓到。不影響本次判定，不需要改。
- **`insert_market_summary` 的 Protocol 簽章 `-> None` 與 `LocalSQLiteBackend` 既有實作 `-> int`（回傳 `lastrowid`）不一致**：這是 contract D1 明確指定的 DynamoDB 簽章（本 PR 只新增 DynamoDB 端），且 local 端這個型別提示落差是 ADR-0004 就存在的既有狀態，不是本次改動引入，不在本 PR 範圍內，僅供未來留意。

## What looks good
- **特別查核點 1（schedule 狀態）**：`infra/template.yaml:313` 的 `SchedulerFunction` `Daily` event 逐字確認仍是 `State: DISABLED`，且 diff 只新增了 `NewsTable` 的 `Globals.Environment` 與 `Policies` 兩處，未觸碰 `Events.Daily` 區塊——本 PR 對雲端排程狀態零改動。
- **特別查核點 2（零 production 行為變更）**：`git diff main` 逐檔核對，除刻意移除的死碼外，沒有動到現行雲端 processor（`processor_handler.py` 未變）或現行本地 launchd 路徑（`__main__` CLI 的 `sync` 分支與預設 `_run()` 行為不變，local 端 `_notify_local`/`sync_earnings` 邏輯完全未改）。`market_summary.py` 本身零改動（contract 允許 1 行例外，generator 選擇完全不改，更保守）。
- **特別查核點 3（死碼移除乾淨）**：`_notify_live`、`PROMPT_TEMPLATE`、`selected_backend_name`/`get_watchlist`/`get_all_active` import 移除後逐一確認其餘 import 仍被使用（`run_agent`→`_company_summary`、`send_message`→`_notify_local`、`get_earnings_calendar`→`sync_earnings`），無殘留 orphan；`DEFAULT_WATCHLIST` 本身在 `watchlist.py`（seed 用）與 `backend.py` 兩個 `seed_watchlist_if_empty` 內仍正確保留，只移除了 `get_watchlist(None)` 內的過渡引用。
- **特別查核點 4（generator 自行擴大的三處取捨）**：`Globals.Environment.NEWS_TABLE` 一行是 `SchedulerFunction` 讀寫 `NewsTable` 的必要前提（沒有這行 boto3 resource 在 Lambda 內會拿不到表名)，屬 D5 IAM 補齊的自然延伸，合理且範圍精準；既有測試從 `scheduler_handler.handler({}, CTX)` 改呼叫 `scheduler_handler._run()`（本地測試組）是必要修正——舊 `handler()` 在 dynamodb 分岔移除後直接呼叫會誤觸新的三階段 pipeline，改呼叫 `_run()` 才是測試原意要驗證的「local notify」邏輯本身；`watchlist.get_watchlist` 包裝函式改為必填是 D4 要求的直接連動，且呼叫端（`processor_handler.py:73`）本來就一律帶 `chat_id`，無破壞性。三處皆手術式、有明確理由、無副作用外溢。
- **特別查核點 5（moto 覆蓋與容錯語意）**：新 backend 方法（`insert_news`/`list_news_since`/`insert_market_summary`）各自有專屬測試且斷言具體值（非泛泛 truthy）；`handler()` 的階段容錯測試明確驗證「某階段失敗 → 其餘階段仍被呼叫 → 最終仍 raise」而非只測 happy path；`push_daily_summary` 在 dynamodb backend 下的端到端測試（`test_push_daily_summary_end_to_end_on_dynamodb_backend`）證明 `refresh_news → insert_news → list_news_since → select_news → generate_market_summary → push` 整條鏈路在雲端 backend 下真正跑通，且斷言 `row["id"] == row["headline"]`，直接對應 D1 的關鍵設計決策（避免 `market_summary.py` 需要修改）。
- IAM policy 補齊精準對應 contract 範例（`DynamoDBCrudPolicy: TableName: !Ref NewsTable`），未夾帶其他 policy 調整。
- Protocol docstring、模組 docstring 的更新內容與 ADR-0006 §2/§4 的措辞精確對應，未見過時或矛盾描述殘留。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無阻擋性問題，可直接進入下一階段（commit / push / PR）。
