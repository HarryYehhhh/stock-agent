---
name: monitoring-engineer
description: >-
  負責線上系統診斷、CloudWatch 日誌分析、DynamoDB 狀態查詢、告警排查與 Incident 根因定位。
---

# Monitoring Engineer Workflow

負責線上系統的日常監控、Incident 排查與可觀測性分析。

## 參考文件
- [docs/runbook.md](file:///Users/yehhungwei/stock-agent/docs/runbook.md)：監控重點與 Incident 排查指南。

## 排查操作指引

### 1. Telegram Bot 無回應排查清單
依序檢查以下鏈路：
1. `getWebhookInfo`：檢查 Telegram 端是否有最後報錯訊息。
2. API Gateway：檢查是否有 4xx/5xx 或 latency 異常。
3. WebhookFunction：檢查 invocations 與 error logs。
4. SQS & DLQ：檢查訊息堆積 (`ApproximateNumberOfMessagesVisible`) 或 DLQ 告警。
5. ProcessorFunction：檢查 CloudWatch logs 是否有 Groq / Finnhub / DynamoDB / Telegram 錯誤。

### 2. 回答內容錯誤但系統未報錯（幻覺 / 資料源追查）
1. **分析錯誤回答形狀**：推論該次回答理論上應呼叫的工具。
2. **查詢 traces 表**：
   比對 `chat_id` 與時間序列下的 `tools[]` 呼叫記錄（包含 `name`、`args`、`ok`、`duration_ms` 等）。
3. **區分 Tool 呼叫的三層次**：
   - 結構化 trace (`tools[]`)。
   - Processor 執行 log（`tool %s succeeded` / `tool %s failed`）。
   - 上游拒絕（Groq 回傳 400 `tool_use_failed`，此時不會進入本地 tool log）。
4. **比對 conversations 表**：尋找同題目的成功回答作為對照組，進行 diff 分析。

### 3. DynamoDB 本機唯讀查詢
免登入 AWS Console，可直接透過唯讀指令檢視正式資料表狀態：
```bash
make ddb-tables                    # 列出所有資料表
make ddb-scan TABLE=subscribers    # 掃描並轉為 JSON
make ddb-count TABLE=subscribers   # 計算筆數
```

### 4. 規則與約束
- 排查時至少由兩個獨立資料源交叉驗證（如 traces 表 + CloudWatch logs + DynamoDB `updated_at`）。
- 若確認故障根因為程式 Bug 或 IAM 權限遺漏，必須整理出最小可行動根因，並切換至 `feature` 或 `sprint` 工作流處理修復。
