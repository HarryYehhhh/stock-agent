# Spec: Local-first storage before DynamoDB upload

## Goal
讓本地開發與測試先把 bot 狀態寫入地端儲存，驗證通過後再用明確命令上傳至 DynamoDB，同時保留 Lambda production 直接使用 DynamoDB 的既有行為。

## User stories
- As a developer, I want local runs to persist conversations, subscribers, and Telegram update dedup state on my machine, so that I can test without touching real DynamoDB.
- As a developer, I want an explicit upload command that copies validated local state into DynamoDB, so that production data changes happen only after tests pass.
- As a maintainer, I want the existing handler APIs to keep working, so that webhook, processor, scheduler, and tests do not need broad rewrites.

## Acceptance criteria
- [ ] Local / non-Lambda execution defaults to a local persistent backend when no explicit production storage backend is selected.
- [ ] Lambda execution defaults to DynamoDB and preserves the current table names, key shapes, TTL fields, and SQS behavior.
- [ ] A developer can override backend selection with an environment variable documented in `.env.example`.
- [ ] Local backend supports the existing public functions: conversation `save_message`, `get_history`, `build_messages`; subscriber `subscribe`, `unsubscribe`, `get_all_active`; dedup `claim_update_id`.
- [ ] Local backend preserves current observable behavior: conversation history is returned oldest-first with a limit, subscribers can be activated/deactivated, and duplicate `update_id` claims return `False`.
- [ ] Upload command reads the local store and writes the equivalent rows to the three DynamoDB tables; it supports a dry-run mode that reports counts without writing.
- [ ] Upload command refuses to run against DynamoDB unless explicitly requested via CLI flag or environment variable, so tests cannot accidentally hit real AWS.
- [ ] Tests use a temporary local store path and never write to a developer's real local data file.
- [ ] Existing `make test` passes with no calls to real AWS, Gemini, Finnhub, or Telegram.

## Non-goals
- 不建立雙寫、背景同步、衝突合併、DynamoDB 到本地的下載/還原機制。
- 不改 Telegram webhook API、SQS queue contract、Gemini tool-use loop、Finnhub tools 或 scheduler 的推播內容。
- 不改 production DynamoDB table schema 或 SAM resource names。
- 不保證本地儲存支援多機共享或跨 process 高併發寫入；本地開發用途只需支援單機流程。
- 不把外部 smoke test 設為 blocking；這次不得呼叫真 Telegram、Gemini、Finnhub 或真 AWS。

## Design Pattern Fit
- Pattern: 最小 Strategy-like storage backend。
- 邊界：只包住 `src/agent/db/` 的 persistence backend 選擇；handler、agent loop、tools 不應知道 backend 實作。
- 為何不是預設不用 pattern：目前有兩個真實變化點（local store 與 DynamoDB），且測試需要能替換儲存位置。若每個 db module 用分散的 `if local else dynamodb`，會重複 backend 判斷並讓測試邊界模糊。
- 為何 simple function/dataclass/explicit DI 不夠：單純新增 helper function 可以減少少量重複，但無法集中表達三個資料集合的一致 backend 選擇、dry-run upload 與 cache reset 行為；dataclass 只能承載設定，不處理行為替換；每個 handler 顯式注入會擴大修改面。
- 驗收測試：同一組 conversation/subscriber/dedup 行為測試至少覆蓋 local backend；upload command 的 integration test 使用 temporary local store + moto DynamoDB，驗證 DynamoDB rows 等價寫入。

## Integration Acceptance
Integration Required: true

Scenario 1: local webhook flow writes local dedup state and queues one message
- Given `STORAGE_BACKEND=local` and `LOCAL_STORE_PATH` points to a temporary file, and SQS is provided by the existing test fixture
- When the webhook handler receives the same Telegram update twice through its public Lambda handler entry
- Then the first call enqueues exactly one SQS message, the second call returns 200 without enqueueing another message, and the local store contains the claimed `update_id`
- Entry: `agent.webhook_handler.handler`
- Fixture: pytest temporary directory, existing AWS/SQS moto fixture, fake Telegram webhook secret
- Observable result: SQS message count and local dedup record
- Cleanup: pytest temporary directory removal and cached backend/table reset
- Command: `PYTHONPATH=src uv run pytest tests/ -v`

Scenario 2: processor and scheduler share local subscriber state
- Given `STORAGE_BACKEND=local` and `LOCAL_STORE_PATH` points to a temporary file
- When processor handles `/subscribe`, then scheduler reads active subscribers
- Then scheduler sees the subscribed chat id through `get_all_active` without DynamoDB access
- Entry: `agent.processor_handler._process_update` and `agent.scheduler_handler.handler`
- Fixture: pytest temporary directory, monkeypatched agent/Telegram/Finnhub functions, no real external APIs
- Observable result: scheduler sends to the subscribed chat id when earnings data is supplied by fakes
- Cleanup: pytest temporary directory removal and cached backend reset
- Command: `PYTHONPATH=src uv run pytest tests/ -v`

Scenario 3: upload command copies local data to DynamoDB
- Given a temporary local store with conversations, subscribers, and dedup rows, and moto DynamoDB tables with the current schemas
- When the upload command runs in dry-run mode
- Then it reports row counts and writes nothing to DynamoDB
- When the upload command runs with the explicit write flag
- Then DynamoDB contains equivalent rows for all three tables, including TTL fields where applicable
- Entry: command module under `src/agent/db/`
- Fixture: pytest temporary directory and existing moto DynamoDB table fixture
- Observable result: command output/return code and DynamoDB table contents
- Cleanup: pytest temporary directory removal and moto teardown
- Command: `PYTHONPATH=src uv run pytest tests/ -v`

## E2E Acceptance
E2E Required: false

原因：repository 目前沒有受控的完整公開 API/UI/CLI E2E harness，也沒有 AGENTS.md 定義的 E2E start/reset/stop 指令。`make dev` 可啟動 FastAPI local server，但會需要 Telegram-style webhook input 與外部 API fakes 才能成為穩定 E2E；本 sprint 以 integration tests 覆蓋跨 handler/filesystem/SQS/DynamoDB 行為。

External Smoke: not requested / non-blocking

## Open questions
- 是否需要在未來 sprint 支援 DynamoDB 到 local store 的下載或雙向同步？本 sprint 假設不需要。
- 上傳前是否要強制自動執行 `make test`？本 sprint 假設 upload command 只提供 dry-run/write guard，不在命令內自動跑測試，避免 command 隱含執行時間與環境副作用。
