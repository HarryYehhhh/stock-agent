# QA Report: sync-local-to-dynamodb

- **Contract**: 無正式 contract，對照使用者需求（SQLite → DynamoDB 資料同步補完，範圍見任務描述）
- **Date**: 2026-07-24
- **Verdict**: ✅ Pass

## 需求 checklist

| 項目 | Status | Note |
|---|---|---|
| 1. `infra/template.yaml` 新增 `NewsTable`（`${AppName}-news`），支援 `published_at` 起算查詢 + headline 去重 + TTL | ✅ | PK=`headline`（對齊 SQLite `headline TEXT UNIQUE`，去重語意一致）；GSI `published-at-index`（HASH=`bucket`固定值`"news"`、RANGE=`published_at`）支援「近期新聞」range query，對齊 `list_news_since`；`TimeToLiveSpecification` 已設 |
| 1b. 不改 Lambda IAM（PR-3 範圍） | ✅ | `git diff` 確認 `SchedulerFunction`/其他 Lambda 的 `Policies` 區塊零改動，`NewsTable` 未出現在任何 Policy 引用中 |
| 2. SchedulerFunction 每日排程補 `State: DISABLED` | ✅ | 加在唯一的 `Daily` ScheduleV2 event 下，位置正確；`ScheduleExpression`/`ScheduleExpressionTimezone` 未變動 |
| 3. `upload_local_store.py` 擴充上傳 earnings_calendar / company_summaries / news | ✅ | dry-run 預設（`main([])` 不寫入）、`--write` 才實寫；Decimal 轉換（`eps_estimate`/`revenue_estimate` 用 `Decimal(str(...))`，與既有 `DynamoDBBackend.upsert_earnings_calendar` 手法一致）；TTL 常數對齊 `backend.py` 既有 `EARNINGS_CALENDAR_TTL_SECONDS`/`COMPANY_SUMMARIES_TTL_SECONDS`，新增 `NEWS_TTL_SECONDS`（14 天）；nullable 欄位（`extended_hour`/`eps_estimate`/`revenue_estimate`/`symbol`/`url`）用 `if row.get(...) is not None` 省略而非寫入 `None`；冪等性由 `put_item` overwrite-by-key 保證，PK 與 infra 表定義一致（earnings_calendar: symbol+earning_date；company_summaries: symbol+earning_date；news: headline） |
| 4. 新增 `sync-dynamodb` make target；`run-daily.sh` 串接第四步 | ✅ | Makefile `.PHONY` 已補上；`sync-dynamodb` 呼叫 `upload_local_store --write`；`run-daily.sh` 用既有 `&&` 串接風格接上第四步，與前三步失敗處理邏輯一致（任一步失敗、後續不執行、不告警只留 log）；`docs/local-daily-schedule.md` 同步更新步驟數與新增「AWS credentials」限制說明 |
| 5. moto 測試覆蓋新上傳邏輯（含 news 表）與冪等性；既有測試不壞 | ✅ | `tests/conftest.py` 新增 `test-news` moto 表（含 GSI）；`tests/test_upload_local_store.py` 新增/改寫涵蓋 dry-run 計數、`--write` 三表寫入內容（含 Decimal 值、nullable 欄位省略驗證）、`test_upload_write_is_idempotent`（連跑兩次 `--write`，三表 Count 皆維持 1）；既有 `test_upload_write_failure_returns_nonzero_and_preserves_local_store` 仍通過 |
| 排除項：market_summaries 不同步 | ✅ | `upload_local_store.py` 未觸碰 market_summaries；`export_rows()` 也未新增該表 |
| 排除項：DynamoDBBackend 不新增 news 讀取方法 | ✅ | `git diff` 確認 `DynamoDBBackend` 類別零改動，本次全部新增集中在 `LocalSQLiteBackend.export_rows()` 與模組層 TTL 常數 |
| 排除項：不執行 make deploy | ✅ | 未見任何部署動作紀錄 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`
- 沙盒內結果：146 passed, 12 errors（既有 `tests/test_local_integration.py` 因 `ThreadingHTTPServer` socket bind 沙盒限制，與本次改動無關，歷史 QA report 已多次記錄同一限制）
- 非沙盒重跑結果：**158 passed, 0 failed**（與 generator 回報一致）
- 失敗測試：無
- 既有 suite regression：✅ 無

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
- **docs/decisions/0004-dynamodb-production-tables.md:44-45**：ADR-0004 §9 明文記載「`upload_local_store.py` 只搬 watchlist，不上傳 earnings_calendar / earnings_notifications / company_summaries」，本次依使用者新指示推翻其中兩項（改為上傳 earnings_calendar、company_summaries；earnings_notifications 仍排除，維持不變）。generator 已在實作摘要中主動揭露此偏差，但 ADR 文件本身未更新，目前處於「決策文件與程式碼行為不一致」狀態，未來讀者對照 ADR 會誤以為程式碼尚未支援這兩表的同步。
  建議修法：於 ADR-0004 §9 補一段 Update/Addendum 註記（保留原決策紀錄，另加「2026-07-24 起因使用者需求變更，upload_local_store 已擴充上傳 earnings_calendar/company_summaries，理由：契合『Finnhub → SQLite → DynamoDB』每日同步的定案資料流」），不需要撤銷整條 ADR，只要讓文件反映現況即可。此項不影響本次功能正確性，可視為後續 P1 任務處理，不阻塞合併。

### 🟢 Suggestions
- **src/agent/db/upload_local_store.py:109, 125**：`now = int(time.time())` 只算一次，之後 earnings_calendar / company_summaries / news 三表都用同一個 `now` 算 TTL，理論上不同表的絕對到期時間會有微秒級誤差可忽略；純粹一致性觀察，非問題。
- **infra/template.yaml NewsTable GSI**：`published-at-index` 用固定值 `bucket="news"` 當 HASH key 做「假分區」以支援跨全表的 `published_at` range query，這是常見的 DynamoDB pattern（單一邏輯分區、GSI 專供 range scan），符合現有資料量級（新聞快取、14 天 TTL）；若未來新聞量顯著增長可能需要真正分桶（如按日期分區），目前規模下不需要，僅供未來參考。

### ✅ What looks good
- `NewsTable` PK 選 `headline` 精準對齊 SQLite `headline TEXT NOT NULL UNIQUE` 的既有去重語意，`insert_news` 的 `INSERT OR IGNORE` 行為與 DynamoDB `put_item` overwrite-by-key 在「headline 去重」這件事上語意一致。
- Decimal 轉換手法（`Decimal(str(x))`）與既有 `DynamoDBBackend.upsert_earnings_calendar`（`backend.py:897,899`）完全一致，沒有另創一套轉換邏輯，維持 float↔Decimal parity 慣例。
- nullable 欄位一律用 `if ... is not None` 判斷式省略寫入，而非寫入 `None`（DynamoDB 對 `None` value 的欄位仍會建立空屬性，省略寫入是正確作法），並且有測試（`ai["symbol"] not in item`、`ai["url"] not in item`）鎖住此行為。
- 冪等性測試（`test_upload_write_is_idempotent`）直接驗證「連跑兩次 `--write`，三表筆數不變」而非只斷言不報錯，是有意義的行為驗證。
- `run-daily.sh` / `Makefile` / 文件三處改動風格與既有三步驟（sync-earnings/notify-earnings/daily-summary）完全一致，沒有引入新的錯誤處理風格或分支邏輯。
- 範圍精準：`git diff --stat` 顯示僅 9 個檔案改動，且逐一核對後皆與需求描述的 5 項對應；`DynamoDBBackend` 類別、`scheduler_handler.py`、`processor_handler.py`、Lambda IAM Policies 皆零改動，符合明確排除項。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：
  1.（非阻塞）補一段 ADR-0004 §9 的 Update 註記，讓文件反映「earnings_calendar/company_summaries 現已納入 upload_local_store」的現況，避免未來讀者誤判範圍。
  2. 無其他待辦。
