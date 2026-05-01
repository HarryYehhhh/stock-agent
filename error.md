# 部署除錯紀錄

本文件紀錄 Stock Earnings Agent 從開發到部署過程中遇到的所有錯誤、除錯過程與解決方案。

---

## 1. Lambda 環境變數使用保留字 `AWS_REGION`

**錯誤訊息：**
```
Lambda was unable to configure your environment variables because the environment
variables you have provided contains reserved keys that are currently not supported
for modification. Reserved keys used in this request: AWS_REGION
```

**發生時機：** 第一次 `sam deploy`，CloudFormation 建立 Lambda Function 時失敗，整個 stack rollback。

**根因：** SAM template (`infra/template.yaml`) 的 `Globals.Function.Environment.Variables` 中設定了 `AWS_REGION: !Ref Region`，但 `AWS_REGION` 是 Lambda 執行環境的保留變數，AWS 不允許使用者自行設定。

**除錯過程：**
1. `aws cloudformation describe-stacks` 發現 stack 狀態為 `ROLLBACK_COMPLETE`
2. 用 `aws cloudformation describe-stack-events` 查詢 `CREATE_FAILED` 事件，找到 `SchedulerFunction` 的失敗原因指向 `AWS_REGION` 保留字

**解決方式：** 從 template.yaml 移除 `AWS_REGION` 環境變數。Lambda 執行時本身就會自動注入 `AWS_REGION`，程式碼中 `os.environ.get("AWS_REGION")` 可以直接讀到，不需要額外設定。

**修復後處理：** 因為 stack 處於 `ROLLBACK_COMPLETE` 狀態無法直接更新，必須先 `aws cloudformation delete-stack` 刪除後重新部署。

**學到的事：** 部署前可以用 `sam validate` 驗證 template，但保留字的問題只有在 CloudFormation 實際建立資源時才會報錯。應熟悉 Lambda 的[保留環境變數清單](https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars.html#configuration-envvars-runtime)。

---

## 2. Telegram Webhook 回傳 404 Not Found

**錯誤訊息：**
```json
{
  "last_error_message": "Wrong response from the webhook: 404 Not Found",
  "pending_update_count": 3
}
```

**發生時機：** 部署成功後，Telegram Bot 傳訊息沒有反應。透過 `getWebhookInfo` API 發現錯誤。

**根因：** SAM template 中 API Gateway 的 `StageName` 設為 `prod`，實際 URL 路徑需要包含 stage prefix `/prod/webhook`。但 CloudFormation Outputs 輸出的 URL 是 `/webhook`（不含 stage），導致 `setWebhook` 設定了錯誤的 URL。

**除錯過程：**
1. Bot 無回應 → 用 Telegram `getWebhookInfo` API 查詢 webhook 狀態
2. 發現 `last_error_message: "404 Not Found"` 和 `pending_update_count: 3`
3. 用 `aws apigatewayv2 get-routes` 確認路由 `POST /webhook` 存在
4. 用 `aws apigatewayv2 get-stages` 確認 stage 名稱是 `prod`
5. 推斷完整 URL 應該是 `/prod/webhook` 而非 `/webhook`

**解決方式：** 重新呼叫 `setWebhook` 使用正確的完整 URL：
```
https://{api-id}.execute-api.{region}.amazonaws.com/prod/webhook
```

**學到的事：** API Gateway HTTP API 的 `$default` stage 不需要路徑前綴，但自訂的 stage（如 `prod`）需要。設定 webhook 前應先用 `curl` 測試 URL 是否回 200，而不是直接設定。

---

## 3. Anthropic API 餘額不足

**錯誤訊息：**
```
anthropic.BadRequestError: Error code: 400 - {'type': 'error', 'error': {
  'type': 'invalid_request_error',
  'message': 'Your credit balance is too low to access the Anthropic API.
              Please go to Plans & Billing to upgrade or purchase credits.'
}}
```

**發生時機：** Webhook 和 SQS 串接正常，processor Lambda 被觸發，但呼叫 Claude API 時失敗。

**除錯過程：**
1. Bot 收到訊息但不回覆 → 檢查 CloudWatch Logs
2. `aws logs get-log-events` 查看 processor Lambda 日誌
3. 發現 `run_agent()` 呼叫 Anthropic API 時拋出 `BadRequestError`
4. 錯誤訊息明確指出是帳戶餘額不足

**解決方式：** 改用 Google Gemini API（`gemini-2.5-flash`），避免付費門檻。涉及的修改：
- `pyproject.toml`：`anthropic` → `google-genai`
- `agent.py`：整個改寫為 Gemini SDK 的 tool-use loop
- `config.py`：`anthropic-api-key` → `gemini-api-key`
- `Dockerfile`：更新 pip install 的套件
- SSM Parameter Store：新增 Gemini key，刪除 Anthropic key
- 測試：更新所有 mock 結構

**學到的事：** 應在開發初期就確認 API 的計費方式和免費額度。在架構上抽象 LLM client 可以讓日後切換模型更容易。

---

## 4. Gemini Tool-Use 對話順序錯誤

**錯誤訊息：**
```
google.genai.errors.ClientError: 400 INVALID_ARGUMENT.
'Please ensure that function call turn comes immediately after a user turn
 or after a function response turn.'
```

**發生時機：** 切換到 Gemini 後，agent 成功呼叫了第一個 tool，但在把 tool 結果送回 Gemini 做第二輪推理時失敗。

**根因：** `agent.py` 的 tool-use loop 中，第一輪把 user message 加到 `contents` 傳給 Gemini，但在後續迴圈中用了另一個變數 `gemini_history`，導致 user message 丟失。Gemini 嚴格要求對話順序：`user → model(function_call) → user(function_response) → model`，缺少初始 user turn 就違反了這個規則。

**除錯過程：**
1. 查看 processor Lambda 日誌，發現兩個錯誤：
   - 第一個：Finnhub `eps-estimate` 回 403（見下一條）
   - 第二個：Gemini 回 400 `INVALID_ARGUMENT`
2. 錯誤訊息指向「function call turn 必須在 user turn 或 function response turn 之後」
3. 回頭檢視 `agent.py` 程式碼，發現 `iteration == 0` 時把 user message 加到臨時的 `contents` 變數，但 tool 結果卻被 append 到 `gemini_history`（不包含 user message）

**問題程式碼：**
```python
# Bug: iteration > 0 時 gemini_history 不包含原始 user message
for iteration in range(MAX_TOOL_LOOPS):
    contents = gemini_history + [
        Content(role="user", parts=[Part.from_text(text=user_message)])
    ] if iteration == 0 else gemini_history  # ← 第二輪起 user message 消失

    response = client.generate_content(contents=contents, ...)

    if function_calls:
        gemini_history.append(candidate.content)        # model turn
        gemini_history.append(Content(role="user", ...)) # function response
        continue  # ← 回到迴圈頂部，但 gemini_history 缺少原始 user message
```

**修正後：**
```python
# Fix: 迴圈前就把 user message 加入 contents，整個迴圈共用同一個 list
contents: list[Content] = [...]  # history
contents.append(Content(role="user", parts=[Part.from_text(text=user_message)]))

for _ in range(MAX_TOOL_LOOPS):
    response = client.generate_content(contents=contents, ...)

    if function_calls:
        contents.append(candidate.content)        # model turn
        contents.append(Content(role="user", ...)) # function response
        continue  # ← contents 完整保留了 user → model → function_response 的順序
```

**學到的事：** Gemini 對對話順序的驗證比 Claude 嚴格。Claude 的 `messages` API 允許較彈性的訊息結構，但 Gemini 要求嚴格交替的 `user ↔ model` 順序。切換 LLM provider 時不能只改 SDK 呼叫方式，必須理解各家對對話結構的不同要求。

---

## 5. Finnhub EPS Estimate API 免費方案不支援

**錯誤訊息：**
```
httpx.HTTPStatusError: Client error '403 Forbidden' for url
'https://finnhub.io/api/v1/stock/eps-estimate?symbol=AAPL&token=...'
```

**發生時機：** Agent 呼叫 `get_analyst_estimates` tool 時，第一個 API (`/stock/recommendation`) 成功，但第二個 API (`/stock/eps-estimate`) 回 403。

**根因：** Finnhub 的 `/stock/eps-estimate` endpoint 需要付費方案，免費 API key 無權存取。原本程式碼對兩個 API 都呼叫 `raise_for_status()`，第二個失敗就直接拋出例外，導致整個 tool 回傳 error，agent 無法取得任何分析師資料。

**除錯過程：**
1. CloudWatch 日誌中看到 `tool get_analyst_estimates failed` 的 traceback
2. 錯誤指向 `estimates.py:32` 的 `eps_resp.raise_for_status()`
3. URL 中可以看到是 `/stock/eps-estimate` endpoint 回 403
4. 查 Finnhub 文件確認該 endpoint 需要 Premium 方案

**解決方式：** 將 `eps-estimate` 的呼叫包在 `try/except` 中，失敗時 graceful fallback：
```python
eps_data: list[dict] = []
try:
    eps_resp = client().get("/stock/eps-estimate", ...)
    eps_resp.raise_for_status()
    eps_data = eps_resp.json().get("data", [])
except Exception:
    log.warning("eps-estimate unavailable for %s (likely free-tier limitation)", symbol)
```
回傳結果中用 `eps_estimates_note` 欄位告知 agent 這項資料不可用，讓 agent 能基於可用的 recommendation 資料組織回覆，而不是整個 tool 失敗。

**學到的事：** 呼叫外部 API 時，不應假設所有 endpoint 的權限等級一致。同一個 API provider 的不同 endpoint 可能有不同的付費門檻。對於非核心資料來源應做 graceful degradation，讓系統在部分資料缺失時仍能運作。

---

## 6. Docker Build 找不到 `config/` 目錄

**錯誤訊息：**
```
COPY failed: file not found in build context or excluded by .dockerignore:
stat config/: file does not exist
```

**發生時機：** `sam build` 執行 Docker build 時，`COPY config/ ./config/` 步驟失敗。

**根因：** `config/sector_symbols.json` 在 Claude Code 的 sandbox 模式下被寫入到受限目錄，sandbox 結束後檔案被清理。多次用 `Write` tool 建立檔案後，用 `ls` 確認存在，但下一個指令執行時檔案已消失。

**除錯過程：**
1. `sam build` 報 `COPY failed: config/ does not exist`
2. 手動 `ls` 確認目錄不存在
3. 重新建立檔案後再跑 build，仍然失敗
4. 發現 sandbox 模式下寫入的檔案生命週期有限
5. 改用 `dangerouslyDisableSandbox=true` 執行 `mkdir` 和寫檔，檔案才能持久保留

**解決方式：** 使用 `dangerouslyDisableSandbox=true` 旗標建立 `config/` 目錄和 `sector_symbols.json`，確保檔案寫入到實際檔案系統而非 sandbox 暫存區。

**學到的事：** 在 CI/CD 或受限環境中，注意檔案系統的寫入權限和持久性。確保 build 所需的所有靜態資源都被 commit 到 git，而非依賴執行時動態產生。

---

## 錯誤排查 Cheat Sheet

| 症狀 | 第一步 |
|---|---|
| Bot 完全沒反應 | `getWebhookInfo` 查看 `last_error_message` |
| Webhook 回 404/502 | 確認 API Gateway stage prefix、Lambda 是否部署成功 |
| Webhook 成功但沒回覆 | 查 processor Lambda 的 CloudWatch Logs |
| CloudFormation ROLLBACK | `describe-stack-events` 找 `CREATE_FAILED` 事件 |
| Lambda 啟動失敗 | 檢查 Docker image 是否包含所有依賴和檔案 |
| Tool 呼叫失敗 | 確認 API key 權限等級、endpoint 是否需要付費方案 |
