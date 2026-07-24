# Contract: DynamoDB → 本地 sqlite 每日下行同步

對應 spec: docs/specs/dynamodb-downsync.md
關聯 ADR: docs/decisions/0005-dynamodb-source-of-truth-downsync.md

## Deliverables

### A. 下行同步模組（DynamoDB → 本地 sqlite）
- [ ] 新增 `agent.db.download_remote_store`（對稱於既有 `agent/db/upload_local_store.py`）：
  - CLI 入口，支援 `--write`（實寫）與省略時 dry-run，與 `upload_local_store` 的 flag 慣例一致。
  - 目標本地 DB 路徑用與 app 一致的解析（`local_store_path()`）。
- [ ] 行為（完整覆蓋 / full mirror）：
  1. 先把 DynamoDB `watchlist`（PK `chat_id`, SK `symbol`, 含 `position`）與 `subscribers`（PK `chat_id`, 含 `subscribed_at`, `active`）**全量 scan 進記憶體**，正確跟進 `LastEvaluatedKey` 分頁。
  2. **兩表 scan 全部成功後**，才在**單一 sqlite transaction** 內：`DELETE FROM watchlist` → 插入 scan 的 watchlist rows；`DELETE FROM subscribers` → 插入 scan 的 subscribers rows；一起 commit。
  3. 型別轉換：`subscribers.active` Bool → INTEGER(1/0)；`position` → INTEGER；`subscribed_at` 缺漏時以現在時間 iso 回填（本地欄位 NOT NULL）。
  4. 只覆蓋 watchlist 與 subscribers；**不得**觸碰本地 conversations / dedup / earnings_calendar / company_summaries / news / market_summaries。
  5. dry-run：只 scan + 印出「將覆蓋」的 rows 數，不動本地，exit 0。
- [ ] 錯誤處理：
  - 任一 DynamoDB scan 例外 → 在動任何本地寫入**前**中止，本地表零改動，印錯誤到 stderr，非 0 exit。
  - transaction 內任一步例外 → rollback，本地兩表維持覆蓋前狀態（非空、非半空），非 0 exit。

### B. 上行調整（移除 cloud-written 表上傳）
- [ ] 修改 `agent/db/upload_local_store.py` 的 `_write_rows`（及 dry-run 的 count 輸出，使其如實反映）：
  - **移除** `conversations`、`subscribers`、`dedup`、`watchlist` 的上傳 put 迴圈。
  - **保留** `earnings_calendar`、`company_summaries`、`news` 上傳不變（TTL、Decimal 轉換、nullable 省略等現有行為原封不動）。
  - 移除因上述刪除而變成 orphan 的 import / 變數（例如不再用到的 table 取得），不順手改其他無關程式。

### C. 排程與工具
- [ ] `Makefile` 新增 `sync-from-dynamodb` target：`PYTHONPATH=src uv run python -m agent.db.download_remote_store --write`（`.PHONY` 同步更新，風格對齊既有 `sync-dynamodb`）。
- [ ] 新增 `scripts/run-downsync.sh`（wrapper，對齊 `scripts/run-daily.sh` 風格）：`cd` 專案根 → `.env` 不存在則 exit 1 → `set -a; source .env; set +a` → `make sync-from-dynamodb`。（下行主要靠 `~/.aws` credentials；source `.env` 維持與既有 wrapper 一致並確保 `LOCAL_STORE_PATH` 等變數載入。）
- [ ] 新增 `scripts/com.earnings-agent.downsync.plist`（獨立 launchd plist）：
  - `Label` = `com.earnings-agent.downsync`；`ProgramArguments` 呼叫 `scripts/run-downsync.sh`。
  - `StartCalendarInterval`：`Hour` 0、`Minute` 0（台北午夜 00:00）。
  - `WorkingDirectory` / `PATH` / log 路徑比照既有 daily plist（log 可導到 `.local/logs/downsync.log`），`RunAtLoad` = `false`。
  - `plutil -lint` 需通過。
- [ ] 文件：更新 `docs/local-daily-schedule.md`（新增 00:00 下行排程小節：安裝 / unload-load / 看 log / 手動觸發 `launchctl start com.earnings-agent.downsync`；說明兩排程分工與「00:00 下行 → 10:30 上行只搬快取」的先後與時區）。若上行步驟描述提及 watchlist/subscribers 上傳，改為只述 earnings_calendar/company_summaries/news。

## Out of scope
- 不改 `seed_watchlist_if_empty`（spec 決策 5：結論是不需要改）。
- 不下行 conversations / dedup / 快取表。
- 不啟用雲端 EventBridge、不 `make deploy`、不改 `infra/template.yaml`（所需 DynamoDB 表皆已存在）。
- 不改 DynamoDBBackend / LocalSQLiteBackend 既有讀寫方法語意（下行模組自行對本地表做 DELETE+INSERT，或複用 backend 的既有 connection helper；擇最小實作）。
- 不做即時同步、不做雙向 merge / conflict resolution。

## Tests required（moto mock DynamoDB，慣例見 tests/conftest.py，指令 `PYTHONPATH=src uv run pytest tests/ -v`）
- [ ] **下行完整覆蓋 / 刪除傳播**：本地 watchlist 預置 A,B,C；DynamoDB 只有 A,C → 下行後本地 watchlist 恰為 A,C（B 被刪）。
- [ ] **subscribers 下行 + active 型別**：DynamoDB 有 active=True 與 active=False 各一 → 下行後本地 `active` 分別為 1、0；`get_all_active()` 只回 active 者。
- [ ] **失敗不留半空表（關鍵回歸）**：本地兩表預置 rows，令 INSERT 階段中途失敗（例如注入壞 row / patch）→ 斷言本地兩表仍為覆蓋前的完整 rows（rollback），非空也非半空。
- [ ] **scan 失敗前置中止**：patch DynamoDB scan raise → 本地表零改動、函式回非 0 / 拋出可辨識錯誤。
- [ ] **pagination 正確**：stub / 造出 scan 兩頁（第一頁帶 `LastEvaluatedKey`）→ 斷言兩頁 rows 都寫入本地。
- [ ] **dry-run**：無 `--write` → 本地零改動、印出 count、exit 0。
- [ ] **冪等**：連跑兩次 `--write` → 本地狀態相同。
- [ ] **下行不越界**：下行前後本地 conversations / earnings_calendar / news 等非目標表 rows 不變。
- [ ] **上行回歸（關鍵，明確 user ask）**：本地 sqlite 有 watchlist rows（並可含 subscribers/conversations/dedup rows），跑 `upload_local_store --write`（moto DynamoDB）→ 斷言 DynamoDB `watchlist`、`subscribers`、`conversations`、`dedup` 表在上行後**維持空**（未被本地 stale 副本回寫）；同一測試斷言 earnings_calendar / company_summaries / news 仍正常上傳。此測試在改動前的 code 應會失敗（鎖住「上行不再搬 watchlist」）。

## Definition of done
- 全部 deliverable 打勾。
- `PYTHONPATH=src uv run pytest tests/ -v` 全綠（沙盒若有既有 `finnhub_stub` socket 限制，以非沙盒重跑確認；新測試不得依賴真實網路 / 真實 AWS）。
- evaluator QA report 無 P0/P1。
- 改動手術式：每一改動行可追溯至本 contract；只移除本次改動造成的 orphan。
