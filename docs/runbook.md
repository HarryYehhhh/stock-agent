# Deployment & Monitoring Runbook

部署、監控與線上問題排查的專案級參考。全域的 `infra-engineer` / `monitoring-engineer` agent 啟動時會讀這份文件。

## 環境資訊

- Region：`ap-northeast-1`
- CloudFormation stack：`earnings-agent`
- 部署方式：AWS SAM（`infra/template.yaml`），單一 Dockerfile 透過 `ImageConfig.Command` 區分 handler

| Lambda | Log group |
|--------|-----------|
| `earnings-agent-webhook` | `/aws/lambda/earnings-agent-webhook` |
| `earnings-agent-processor` | `/aws/lambda/earnings-agent-processor` |
| `earnings-agent-scheduler` | `/aws/lambda/earnings-agent-scheduler` |

## 部署後驗證

```bash
# Stack 狀態
aws cloudformation describe-stacks \
  --region ap-northeast-1 \
  --stack-name earnings-agent \
  --query 'Stacks[0].StackStatus' \
  --output text

# Telegram webhook 狀態
curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

Logs 追蹤：`make logs-webhook` / `make logs-processor` / `make logs-scheduler`，或：

```bash
aws logs tail /aws/lambda/earnings-agent-processor --region ap-northeast-1 --since 1h
```

## 查看 DynamoDB 資料（本機唯讀）

免登入 AWS console，用 AWS CLI + jq 直接看正式表（scan 為唯讀，無寫入風險）：

```bash
make ddb-tables                    # 列出本 stack 的所有表（去 prefix）
make ddb-scan TABLE=subscribers    # 掃描並攤平成一般 JSON
make ddb-count TABLE=subscribers   # 只算筆數
```

可覆寫 `REGION` / `DDB_PREFIX`；`ddb-scan` 單次 scan 上限 1MB，大表僅回第一頁。輸出可再接 jq 篩選，例：`make ddb-scan TABLE=subscribers | jq '[.[] | select(.active)]'`。

## ECR lifecycle policy

三個 ECR repo（`processorfunction*repo` / `schedulerfunction*repo` / `webhookfunction*repo`）是 `sam deploy --resolve-image-repos` 建立的 companion stack repo，不在 `infra/template.yaml` 內——SAM 是「先推 image 再建主 stack」，若在同一個 template 定義這些 repo 會造成循環依賴，因此無法搬進 IaC 管理。

沒有 lifecycle policy 時每次 build 都會累積新 image（ECR 費用 $0.10/GB-月），曾一路堆到 89 個舊 image、約 $0.15/月。做法是直接對既有 repo 呼叫 `aws ecr put-lifecycle-policy` 掛上 policy——policy 是掛在 repo 資源上、一次套用永久有效，不需要每次 deploy 重掛。

```bash
make ecr-lifecycle    # 對所有 earningsagente* repo 套用 infra/ecr-lifecycle-policy.json（保留最近 3 個 image，其餘 expire）
```

若 stack 重建（repo hash 會改變、產生新 repo 名稱），要重跑 `make ecr-lifecycle` 讓新 repo 也掛上 policy。ECR 對已標記為 expire 的 image 是非同步清理，套用 policy 後最多 24 小時才會實際刪除，不會馬上生效。

## 已知陷阱

- Telegram webhook URL 必須包含 `/prod/webhook`，少了 `/prod` 會 404；API Gateway 404 時 Lambda 不會有新 log。
- `sam logs --tail` 沒輸出不代表沒有既有 logs，只是在等新事件。
- Scheduler log group 要等第一次排程觸發後才會建立。
- Docker build context 是專案根目錄，不是 `infra/`。
- Lambda timeout 必須小於 SQS visibility timeout。
- EventBridge schedule 用 UTC 時間。

## 監控重點

- **API Gateway**：4xx / 5xx / latency / request count
- **WebhookFunction**：invocations、errors、duration（webhook 應快速回應，不跑長任務）
- **SQS**：`ApproximateNumberOfMessagesVisible`、`ApproximateAgeOfOldestMessage`、DLQ message count（DLQ 有任何訊息都應通知，已由 `earnings-agent-dlq-messages` alarm 涵蓋）
- **ProcessorFunction**：errors、duration 接近 300s timeout——這是 AI agent 出錯的第一現場（Groq API error、Finnhub error、DynamoDB error、Telegram send failure、tool-use loop exception）
- **SchedulerFunction**：daily invocation 是否發生、errors、Telegram push 是否失敗

## Incident 排查

### 使用者說 Telegram 沒回覆

依序檢查：

1. `getWebhookInfo` 是否有 `last_error_message`
2. Webhook URL 是否包含 `/prod/webhook`
3. API Gateway 是否有 4xx / 5xx
4. WebhookFunction 是否有新 invocation
5. SQS queue 是否堆積
6. ProcessorFunction 是否有 error
7. DLQ 是否有訊息
8. Processor logs 是否有 Groq / Finnhub / Telegram send error

### 只有 webhook log、沒有 processor log

可能原因：webhook 沒有成功 enqueue SQS、SQS event source mapping 異常、processor Lambda 權限或 trigger 問題、訊息被 dedup 邏輯擋掉。

### 沒有 scheduler logs

可能原因：scheduler 尚未第一次觸發、schedule disabled、target Lambda 或 IAM role 有問題。先確認 EventBridge schedule 是否 enabled。

### Processor 有 LLM API 錯誤（如 Groq 429/503）

通常是外部服務限流或高負載，不一定是程式或部署錯。確認使用者是否收到友善錯誤訊息；若頻繁發生，考慮走 harness 補 retry/backoff 或 fallback 回覆。

### 回答內容錯誤但系統沒報錯（幻覺／資料源追查）

這類最難查——沒有 alarm 會響，系統照常回應，錯的只是內容本身。範例：2026-07-29 07:00:54 UTC 一次回答同時暴露三個問題——把分析師人數講成百分比（已由 PR #47 移除 `get_analyst_estimates` 修掉）、`search_knowledge_base` 的 `top_k` schema 驗證失敗（已由 PR #48 修掉）、以及把財報日期講錯。財報日期講錯的近因是 `get_earnings_calendar` 因 DynamoDB IAM AccessDenied 拋例外，模型在工具全滅後自行編造日期；AccessDenied 靠當天稍後重新部署補回權限而消失，不是程式修正——**工具失敗時模型會編造內容**這個更深層的問題至今未處理。本節記錄的是排查方法，不是重述那次事故。

**排查順序**

1. 先讀錯誤回答的「形狀」，推論它應該用到幾個資料源／哪些 tool，建立可證偽的預期。
2. 查 traces 表，用 `chat_id` + 時間比對 `tools[]` 是否符合預期；不符就是抓錯請求，不要硬套。
3. 查 conversations 表拉時間序，找對照組——相近問題的成功案例，diff 兩次執行比單案分析強得多。
4. trace 缺席也是證據，但先確認 `save_trace` 是 fail-silent（`src/agent/agent.py:180`，寫入失敗只留 warning、不影響回覆）。
5. 去 CloudWatch 撈該 RequestId 的完整 log，時間窗開窄（±3 分鐘）。
6. **把 log 訊息回溯到發出它的程式碼行再解讀**——不同來源的相似訊息語意可能完全相反。例如 `src/agent/agent.py:114` 的 `tool %s failed` 是 tool 執行例外；`src/agent/tools/earnings.py:105` 的 `earnings calendar cache write failed` 只是快取寫入失敗，tool 本身可能已成功回傳資料——兩者訊息長得像但意義不同。
7. 用獨立的第三個資料源交叉驗證，例如 earnings-calendar 表的 `updated_at` 可佐證某次請求有沒有真的寫入。
8. 用 `filter-pattern` 確認故障是否已終結，回答「現在還壞不壞」。

**判斷「LLM 有沒有呼叫 tool」的三個層次**（只看自己的 tool log 會漏掉第三層）

- 結構化：trace 的 `tools[]`（每筆含 `name`/`args`/`ok`/`duration_ms`/`result_type`/`result_size`）
- 執行 log：`src/agent/agent.py:104` 的 `tool %s succeeded` / `src/agent/agent.py:114` 的 `tool %s failed`
- **上游拒絕**：Groq 回 400 `tool_use_failed`（`src/agent/agent.py:244-264`）——模型發出了呼叫，但被伺服器端 schema 驗證擋掉，`_run_tool` 從未進入，自己的 tool log 一片空白。只看前兩層會誤判成「模型沒叫這個 tool」，往完全錯的方向查。

**判斷「是不是 timeout」**

- 每個 tool 的耗時看 trace 的 `duration_ms`；整次請求看 CloudWatch `REPORT` 行的 `Duration`，對照 Lambda timeout（processor 300s）。
- 依失敗耗時區分性質：timeout 會貼著上限出現；IAM 拒絕／schema 驗證錯誤在毫秒內就回來。
- 誤判成 timeout 會導致去調 timeout、加 retry、查限流，全部白做。

**紀律**

- 推測要標成推測，不要當結論寫進報告。
- 至少兩個獨立資料源交叉驗證才收工。
- 可觀測性設施本身也會壞，而且常與被觀測系統有共同失效來源——2026-07-29 那次事故期間，traces 表恰好因另一起 stale local deploy 事故被刪除，該請求因此無 trace 可查（兩者因果未經查證，僅時間上巧合）——永遠要有第二條路。
