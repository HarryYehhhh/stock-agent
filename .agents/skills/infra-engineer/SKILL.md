---
name: infra-engineer
description: >-
  負責雲端基礎設施部署、SAM 部署後驗證、ECR 生命週期管理與雲端環境狀態確認。若發現需要修改程式碼或 IaC，應回退至 feature 或 sprint 流程處理。
---

# Infra Engineer Workflow

負責 AWS 基礎設施的部署、驗證與維運管理。

## 參考文件
- [docs/runbook.md](file:///Users/yehhungwei/stock-agent/docs/runbook.md)：部署後驗證、已知陷阱與維運操作。
- [docs/deployment-flow.md](file:///Users/yehhungwei/stock-agent/docs/deployment-flow.md)：CI/CD 部署流程。

## 主要操作指引

### 1. 部署前檢查與部署
- 本機部署前確保工作區乾淨且與 `origin/main` 同步（由 `scripts/deploy-guard.sh` 防護）：
  ```bash
  make build
  make deploy
  ```

### 2. 部署後驗證（Post-deployment Verification）
1. **CloudFormation Stack 狀態**：
   ```bash
   aws cloudformation describe-stacks \
     --region ap-northeast-1 \
     --stack-name earnings-agent \
     --query 'Stacks[0].StackStatus' \
     --output text
   ```
2. **Telegram Webhook 狀態**：
   ```bash
   curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
   ```
   確認 `url` 包含 `/prod/webhook` 且 `last_error_message` 為空。
3. **Lambda Logs 追蹤**：
   ```bash
   make logs-webhook
   make logs-processor
   make logs-scheduler
   ```

### 3. ECR Lifecycle Policy 管理
- 防止 ECR 累積過多未使用的 Docker images 產生費用：
  ```bash
  make ecr-lifecycle
  ```

### 4. 規則與約束
- **禁止隨意手動修改程式碼或 IaC**：若在維運或部署檢查中發現架構或程式缺陷，必須回退至 `feature` 或 `sprint` 工作流，由 generator 實作並經 evaluator 驗收後透過 CI/CD 部署。
