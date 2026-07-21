# ADR-0001: Local-first storage backend

- **Date**: 2026-07-20
- **Status**: Proposed

## Context
目前 `src/agent/db/` 的 conversations、subscribers、dedup 三個模組直接使用 DynamoDB。這讓本地開發與測試語意依賴 DynamoDB shape；雖然測試以 moto 模擬 AWS，但使用者希望資料先存在地端，測試沒問題後再上傳至 DynamoDB。

這個決策會影響 database layer 的 backend selection、local development default、upload command 與測試邊界。Production Lambda 仍需要可靠使用 DynamoDB，因為 Lambda local filesystem 不適合作為跨 invocation 的正式狀態來源。

## Decision
在 database layer 引入最小 local-first backend boundary：

- Local / non-Lambda 預設使用地端 persistent store。
- Lambda / production 預設使用 DynamoDB。
- `STORAGE_BACKEND` 可明確覆寫 backend，支援 `local` 與 `dynamodb`。
- `LOCAL_STORE_PATH` 指定地端儲存位置；測試必須使用 temporary path。
- Upload to DynamoDB 必須透過明確 command 執行，預設 dry-run，只有 write flag 存在時才寫入。
- Handler 與 agent 層維持呼叫現有 db public functions，不直接知道 backend 實作。

建議 local store 使用 Python stdlib `sqlite3` 單檔資料庫，因為它可用唯一鍵約束表達 dedup first-claim-wins，並可用 SQL ordering/limit 保留 conversation history 語意，不需新增 dependency。

## Consequences
正面：
- 本地開發預設不碰真 DynamoDB，降低誤寫 production state 的風險。
- 現有 handler public behavior 可以維持，修改面集中在 `src/agent/db/`。
- Upload command 讓「測試後再上傳」成為可驗證、可 dry-run 的手動動作。

負面 / trade-off：
- database layer 會多一個 backend selection 邊界與 cache reset 責任。
- Local store 與 DynamoDB 之間沒有自動同步；開發者必須明確執行 upload。
- 若未來需要多機共享、衝突合併或 production 雙寫，需要新的 ADR 或 supersede 本決策。

## Alternatives considered
- 保持現狀，只用 moto 測試 DynamoDB：修改最少，但不滿足「先存在地端」與避免本地誤寫真 DynamoDB 的目標。
- LocalStack / DynamoDB Local：接近 AWS 行為，但需要額外服務與 setup/reset 指令；目前 AGENTS.md 沒有這些 workflow，對本 sprint 過重。
- JSON 檔案 local store：實作簡單，但 dedup 原子性、排序查詢與 upload mapping 會變得脆弱；需要更多手寫鎖與邊界測試。
- 全面 Repository/service layer 重構：可以統一資料存取，但會擴大 handler 與測試修改面；現有三個 db module 已經是足夠小的 public API。
