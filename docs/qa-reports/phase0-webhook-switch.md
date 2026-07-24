# QA Report: phase0-webhook-switch

- **Contract**: 無 contract（小功能，直接對照原始需求驗收）
- **Date**: 2026-07-23
- **Verdict**: ✅ Pass

## 需求 checklist

| 需求 | Status | Note |
|---|---|---|
| Makefile 新增 `set-webhook-local`（接受 `WEBHOOK_URL`，呼叫 setWebhook 指向 `$(WEBHOOK_URL)/webhook`，帶 `secret_token`） | ✅ | dry-run 確認 `url=https://xxxx.ngrok-free.app/webhook`、`secret_token=fakesecret` 均正確帶出 |
| Makefile 新增 `delete-webhook`（呼叫 deleteWebhook，只需 token） | ✅ | dry-run 確認只檢查 `TELEGRAM_BOT_TOKEN`，未要求 `WEBHOOK_URL`/`TELEGRAM_WEBHOOK_SECRET` |
| 缺參數時比照 `set-webhook` 的 `@test` 風格報錯退出 | ✅ | 實測 `make set-webhook-local`（全缺）、`make set-webhook-local TELEGRAM_BOT_TOKEN=... TELEGRAM_WEBHOOK_SECRET=...`（缺 WEBHOOK_URL）、`make delete-webhook`（缺 token）皆印出對應 `<VAR> missing` 並以非 0 exit code（2）終止，未發出 curl |
| 加進 `.PHONY` | ✅ | 第 1 行 `.PHONY` 已包含 `set-webhook-local delete-webhook` |
| 現有 `set-webhook` 保留不動 | ✅ | `git diff` 確認 38-44 行原始 `set-webhook` target 完全未變動，diff 只新增兩個新 target |
| `docs/deployment-flow.md` 補「本地開發 webhook 切換」說明 | ✅ | 已在「本地開發」段落下新增子小節，含 5 步驟 |
| 文件步驟與 Makefile 實際指令一致 | ✅ | 逐字核對：步驟 1 `make dev`、步驟 2 `ngrok http 8080`、步驟 3 `make set-webhook-local WEBHOOK_URL=https://xxxx.ngrok-free.app`、步驟 5 `make set-webhook WEBHOOK_URL=<API Gateway URL>` / `make delete-webhook`，均與 Makefile target 名稱、參數名稱一致 |
| 文件內容技術正確性 | ✅ | 額外查核「本地 `webhook_handler` 會驗證此 secret」的說法：`make dev` 啟動的 `local_server.py` 的 `/webhook` route 直接 delegate 至 `webhook_handler.handler`（`src/agent/local_server.py:22,35`），而 `webhook_handler.py:37-39` 確實比對 `X-Telegram-Bot-Api-Secret-Token` header 與 `telegram_webhook_secret()`，描述屬實 |

## Test results

- 測試指令：`PYTHONPATH=src uv run pytest tests/ -v`（本次用 `-q` 精簡輸出）
- 沙盒內結果：123 passed, 12 errors（`tests/test_local_integration.py` 因 sandbox 對 socket bind 有 `PermissionError: Operation not permitted` 限制，屬 sandbox 環境限制，非本次改動導致）
- 非沙盒重跑結果：**135 passed, 0 failed**（以此為準）
- 失敗測試：無
- 既有 suite regression：✅ 無（本次改動僅涉及 Makefile 與 markdown 文件，不影響任何 Python 程式碼路徑，測試結果符合 generator 回報的基準）

## Findings

### 🔴 Critical（Must Fix）
無。

### 🟡 Warnings（Should Fix）
無。

### 🟢 Suggestions
- **Makefile:38-56**：`set-webhook` 與 `set-webhook-local` 的差異只在於 `url` 是否已含 `/webhook` 後綴（前者呼叫方需自行帶完整 URL 含 path，後者自動補 `/webhook`）。這個不對稱本身是需求指定的行為，非 bug，但未來若有人誤用（例如對 `set-webhook` 傳未含 `/webhook` 的 URL）不會有任何錯誤提示。目前文件已用範例值清楚示範兩者用法（`WEBHOOK_URL=<API Gateway URL>` vs `WEBHOOK_URL=https://xxxx.ngrok-free.app`），可接受，不要求修改。
- **docs/deployment-flow.md**：新增小節與既有「本地開發」段落風格（標題階層、條列格式）一致，沿用既有文件慣例，無需改動。

### ✅ What looks good
- 新增的兩個 target 與既有 `set-webhook` 的 `@test -n "$(VAR)" || (echo "..." && exit 1)` 錯誤處理風格完全一致，包含錯誤訊息格式（`"<VAR> missing"`）。
- `.PHONY` 更新正確，未遺漏也未多餘。
- `set-webhook` 完全未被觸碰，符合「現有 target 保留不動」的要求。
- 文件步驟的操作順序（`make dev` → `ngrok http 8080` → `set-webhook-local` → 測試 → 切回 `set-webhook`/`delete-webhook`）合乎邏輯且與 Makefile 實際指令名稱、參數逐字對應。
- 改動範圍精準，僅涉及 Makefile 與一份文件，未動到任何 `src/` 程式碼，無 regression 風險。

## 結論
- 是否可合併：是
- 最該先處理的 1–3 件事：無（本次驗收無需修正項目）
