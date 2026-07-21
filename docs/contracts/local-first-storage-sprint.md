# Contract: local-first-storage-sprint

對應 spec: docs/specs/local-first-storage.md

## Deliverables
- [ ] 在應用程式碼路徑的 database layer（此 repo：`src/agent/db/`）加入 backend selection，讓 local / non-Lambda 預設使用地端儲存，Lambda 預設使用 DynamoDB。
- [ ] 在應用程式碼路徑加入本地 persistent store。建議使用 Python stdlib `sqlite3` 單檔資料庫；若 generator 選其他地端格式，必須在 implementation notes 說明如何滿足 dedup 原子性、history ordering 與 upload mapping。
- [ ] 保留既有 db module public API：`save_message`, `get_history`, `build_messages`, `subscribe`, `unsubscribe`, `get_all_active`, `claim_update_id`。
- [ ] 行為：當 `STORAGE_BACKEND=local` 且 `LOCAL_STORE_PATH=/tmp/path/store.sqlite3`，conversation/subscriber/dedup 寫入與讀取都只使用該 local store。
- [ ] 行為：當 `STORAGE_BACKEND=dynamodb`，三個 db module 使用既有 DynamoDB table name config、key schema 與 TTL 欄位。
- [ ] 行為：當未設定 `STORAGE_BACKEND`，`AWS_LAMBDA_FUNCTION_NAME` 存在時選 DynamoDB；不存在時選 local。
- [ ] 行為：conversation history 回傳最多 `limit` 筆最近訊息，輸出順序仍為 oldest-first，`build_messages` 仍只回 `role` + `content`。
- [ ] 行為：`subscribe(chat_id)` 設定 active true；`unsubscribe(chat_id)` 將 active 設為 false；`get_all_active()` 只回 active true 的 chat ids。
- [ ] 行為：`claim_update_id(update_id)` 第一次回 `True`，同一 local store 中重複 claim 回 `False`。
- [ ] 在應用程式碼路徑提供 upload command module，可由 `PYTHONPATH=src uv run python -m <module>` 執行，將 local store rows 上傳至 DynamoDB。
- [ ] Upload command 支援 dry-run 預設模式，輸出 conversations/subscribers/dedup 將上傳的 row counts，且不寫入 DynamoDB。
- [ ] Upload command 只有在明確 write flag（例如 `--write`）存在時才寫入 DynamoDB；缺少 `LOCAL_STORE_PATH` 或 local store 不存在時，以非 0 exit code 與清楚訊息失敗。
- [ ] Upload command 寫入 DynamoDB 時沿用現有三張表 schema：conversations 使用 `chat_id` + `timestamp`，subscribers 使用 `chat_id`，processed updates 使用 `update_id`。
- [ ] 更新 `.env.example` 記錄 `STORAGE_BACKEND` 與 `LOCAL_STORE_PATH`，並避免放入真實本地資料路徑。
- [ ] 若 local store 預設路徑位於 repo 內，更新忽略規則讓本地資料檔不會被提交。
- [ ] 錯誤處理：不支援的 `STORAGE_BACKEND` 值 → 明確 raise `ValueError` 或 command 以非 0 exit code 失敗。
- [ ] 錯誤處理：upload command 在 write mode 遇到 DynamoDB write error → 非 0 exit code 並保留 local store 不變。

## Out of scope
- 不實作 DynamoDB 下載到 local。
- 不實作雙寫、背景同步、衝突解決或 migration job。
- 不更動 SAM DynamoDB table schema、SQS queue、Telegram webhook request/response shape、Gemini/Finnhub tools。
- 不新增真外部 API smoke test，也不在測試中呼叫真 AWS。

## Design Pattern Fit
- Pattern: 最小 Strategy-like backend boundary。
- 邊界：只在 database layer 封裝 local vs DynamoDB persistence；handler 維持呼叫既有 db functions。
- 為何不用更重的 Repository abstraction：目前 public API 已經是三個小型 db module，generator 應優先用 shared backend selector / protocol 消除 backend 判斷重複，不建立泛用 ORM 或 service layer。
- 為何 simple function/dataclass/explicit DI 不夠：兩個 backend 需要一致處理 cache reset、upload 讀取與三類資料操作；分散 helper 會讓測試必須 patch 內部 client method。這次的驗收測試必須透過 public db functions 與真 local filesystem / moto DynamoDB 驗證。
- 驗收測試：local backend 行為測試、DynamoDB backend regression 測試、upload command integration 測試。

## Tests required
- [ ] Unit tests 覆蓋 backend selection：未設定環境變數時 local/non-Lambda 與 Lambda/DynamoDB default；`STORAGE_BACKEND` override；不支援值失敗。
- [ ] Unit tests 覆蓋 local backend conversation ordering/limit、subscriber active filtering、dedup duplicate claim。
- [ ] Unit tests 覆蓋 upload command argument guard：dry-run default、write flag required、missing local store failure。
- [ ] Integration tests 覆蓋 `STORAGE_BACKEND=local` 下 webhook duplicate update 只 enqueue 一次，並可觀察 local dedup state。
- [ ] Integration tests 覆蓋 processor `/subscribe` 寫 local store，scheduler 讀取同一 local store 的 active subscribers；Gemini、Finnhub、Telegram 全部使用 fake/stub，不呼叫真第三方。
- [ ] Integration tests 覆蓋 upload command 使用 temporary local store + moto DynamoDB：dry-run 不寫入，write mode 寫入三張表。
- [ ] Regression tests 保留現有 DynamoDB backend 行為，使用 moto fixture 驗證三個 db module 仍符合既有測試。

## Verification commands
- Unit / integration test command from AGENTS.md: `make test`
- Single-test command from AGENTS.md: `PYTHONPATH=src uv run pytest tests/test_agent.py -v -k test_name`
- Full explicit pytest command used by contract scenarios: `PYTHONPATH=src uv run pytest tests/ -v`
- Start command from AGENTS.md: `make dev`（FastAPI local server on port 8080；需要 `.env` from `.env.example`）
- Reset command: AGENTS.md 沒有定義 project-level reset command。測試必須使用 pytest temporary directory 與 fixture cleanup；不要在 contract 中假設其他 reset 指令。
- Stop command: AGENTS.md 沒有定義 project-level stop command。手動開發時停止 `make dev` 的 uvicorn process；不要在自動測試中依賴未定義 stop 指令。
- Build command from AGENTS.md: `make build`（若 generator 變更 packaging 或 SAM env 時才需要）

## Integration Acceptance
Integration Required: true

Scenario 1: local webhook dedup with SQS
- Given `STORAGE_BACKEND=local`, `LOCAL_STORE_PATH` points to pytest tmp path, fake webhook secret is configured, and SQS uses the existing moto fixture
- When `agent.webhook_handler.handler` receives two identical Telegram updates
- Then both responses are 200, only one SQS message exists, and the local dedup table/store has the update id
- Cleanup: clear backend/table/client caches and remove tmp path through pytest fixtures
- Command: `PYTHONPATH=src uv run pytest tests/ -v`

Scenario 2: processor writes local subscriber and scheduler reads it
- Given `STORAGE_BACKEND=local`, shared `LOCAL_STORE_PATH`, fake earnings data, fake industry lookup, fake Telegram send, and fake agent summary
- When `agent.processor_handler._process_update` handles `/subscribe` for a chat id and `agent.scheduler_handler.handler` runs
- Then scheduler sends the reminder to that chat id without DynamoDB access
- Cleanup: clear backend caches and tmp path through pytest fixtures
- Command: `PYTHONPATH=src uv run pytest tests/ -v`

Scenario 3: local upload to DynamoDB
- Given temporary local store data and moto DynamoDB tables matching current schemas
- When upload command runs dry-run
- Then stdout or returned summary reports counts and DynamoDB remains empty
- When upload command runs with explicit write flag
- Then DynamoDB rows match local conversations, subscribers, and dedup records
- Cleanup: tmp path and moto teardown
- Command: `PYTHONPATH=src uv run pytest tests/ -v`

## E2E Acceptance
E2E Required: false

Repository 目前沒有 AGENTS.md 定義的 E2E harness、reset command 或 stop command。`make dev` 可作為 manual local server start，但本 sprint 的 blocking gate 是 unit + integration tests，不呼叫真第三方。

External Smoke: not requested / non-blocking

## Definition of done
- 全部 deliverable 打勾。
- `make test` 全綠。
- Upload command dry-run 與 write-mode integration tests 全綠，且測試不碰真 AWS。
- evaluator 的 QA report 無 P0/P1。
- 若新增 local store 預設檔案位置，該路徑已被 `.gitignore` 忽略或位於系統 temp/config 目錄，不會污染 git status。
