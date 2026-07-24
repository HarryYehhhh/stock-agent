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
- **SQS**：`ApproximateNumberOfMessagesVisible`、`ApproximateAgeOfOldestMessage`、DLQ message count（DLQ 有任何訊息都應通知）
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
