# AWS 服務總覽

本專案(Telegram 美股財報 AI agent)採全 serverless 架構,`infra/template.yaml` 實際用到 9 個 AWS 服務。本文按請求流程的角色說明每個服務解決了什麼問題。

## 核心請求鏈路

### 1. API Gateway (HTTP API)

- 提供公開的 HTTPS endpoint(`POST /webhook`)給 Telegram 推送訊息用。
- **解決的問題**:Telegram webhook 需要一個穩定的公網 HTTPS 入口,但專案不想養一台 24 小時開機的伺服器。HTTP API(相較於 REST API)更便宜、延遲更低,適合這種單一路由的場景。

### 2. Lambda(3 個 function,Docker container image 部署)

| Function | Timeout | 職責 |
|----------|---------|------|
| `earnings-agent-webhook` | 10s | 接收 Telegram update,做去重與 rate limit 檢查後丟進 SQS,立刻回 200 |
| `earnings-agent-processor` | 300s | 從 SQS 收訊息,跑 `run_agent()` 的 Groq tool-use loop(查財報行事曆、新聞、報價等),再把繁中回覆送回 Telegram |
| `earnings-agent-scheduler` | 300s | 被 EventBridge 定時觸發,做資料同步與推播 |

- **解決的問題**:訊息量小且零散(個人 bot),用 Lambda 按次計費幾乎免費,不需管理伺服器。三個 function 共用同一個 Dockerfile,靠 `ImageConfig.Command` 切換 handler,維護單一 image 即可。

### 3. SQS(主 queue + DLQ)

- webhook 和 processor 之間的緩衝層。
- **解決的問題**:這是架構的關鍵設計——Telegram webhook 有 60 秒 timeout,但 agent 跑 Groq tool-use loop 可能耗時數十秒到數分鐘。webhook 只花不到 10 秒把訊息塞進 queue 就回應,processor 再慢慢處理(`MaximumConcurrency: 10` 限制併發)。失敗重試 3 次後進 DLQ(保留 14 天),訊息不會無聲消失。

## 資料層

### 4. DynamoDB(11 張表,on-demand 計費)

存對話紀錄、訂閱者、watchlist、財報行事曆快取、新聞快取、Telegram update 去重、rate limit 計數、通知去重、財報前瞻摘要快取、對話 trace、使用者糾正回報(完整 schema 見 `CLAUDE.md`)。

- **解決的問題**:
  - **狀態持久化**:Lambda 是無狀態的,多輪對話歷史必須外存(conversations 表,30 天 TTL)。
  - **外部 API 配額**:Finnhub 和 Groq 都是免費額度,行事曆/新聞/每日摘要都做快取,避免重複打 API。
  - **冪等與防濫用**:`processed-updates` 表擋 Telegram 重送,`rate-limits` 表做每 chat 分鐘窗/日窗限流。
  - 大量使用 **TTL 自動過期**,不用自己寫清理 job;on-demand 計費在低流量下趨近 $0。

## 排程與設定

### 5. EventBridge Scheduler(2 個每日 cron,Asia/Taipei 時區)

- 08:00 `{"mode": "sync"}`:更新財報行事曆與科技股/AI 新聞快取。
- 10:00 `{"mode": "notify"}`:推送 24 小時內發布的 watchlist 財報提醒 + 每日科技股市場摘要。
- **解決的問題**:取代 crontab/常駐排程器,原生支援時區(不用自己算 UTC 換算與日光節約),且帶重試 policy(2 次,最長 1 小時)。

### 6. SSM Parameter Store(`/earnings-agent/` 前綴)+ KMS

- 存 `GROQ_API_KEY`、`FINNHUB_API_KEY`、`TELEGRAM_BOT_TOKEN` 等 secrets,Lambda 用 `ssm:GetParameters` 讀取、KMS 解密(用 AWS 管的 `aws/ssm` key)。
- **解決的問題**:secrets 不進版控也不用明碼塞環境變數;比 Secrets Manager 便宜(標準參數免費)。本機開發則 fallback 到 `.env`。

## 監控告警

### 7. CloudWatch(Logs + 4 個 Alarms)

- 三個 Lambda 的 logs 都進 CloudWatch(`make logs-*` 就是在 tail 這些);alarms 監控 scheduler/processor 錯誤、scheduler 執行時長、DLQ 有訊息堆積。
- **解決的問題**:無伺服器架構沒有機器可以 ssh 進去看,logs/alarms 是唯一的觀測手段。DLQ alarm 特別重要——訊息進 DLQ 代表使用者的訊息處理失敗了 3 次。

### 8. SNS(alarm topic + email 訂閱)

- CloudWatch alarm 觸發時發 email 通知(訂閱信箱透過部署參數傳入,不進版控)。
- **解決的問題**:個人專案不會一直盯 dashboard,出事直接進信箱。

## 隱含依賴

### 9. ECR + CloudFormation(SAM)

- Lambda 用 container image 部署,`sam build/deploy` 會把 image 推到 ECR;整組資源由 CloudFormation stack `earnings-agent` 管理(SAM 是其上的簡化語法)。
- **解決的問題**:Python 依賴(groq SDK 等)打包進 image 避開 Lambda zip 250MB 限制與依賴地獄;IaC 讓整套環境可以一鍵重建、變更可追溯。

## 整體設計思路

所有服務都選了「低流量下免費」的計費模式(Lambda 按次、DynamoDB on-demand、HTTP API、SSM 標準參數),整個月成本約 $0;而 SQS 解耦 + DLQ + 排程重試 + alarms 這組合,讓一個沒有常駐伺服器的系統仍有完整的容錯與觀測能力。
