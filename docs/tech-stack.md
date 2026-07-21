# 技術棧架構說明

本文整理 `earnings-agent` repo 用到的所有技術，以及它們各自在系統裡負責的職責。搭配 `architecture.png` 與 `docs/deployment-flow.md` 一起閱讀。

---

## 1. 一張圖看請求流

```
Telegram User
   │
   ▼ (HTTPS POST /webhook)
API Gateway (HTTP API)
   │
   ▼
WebhookFunction (Lambda, 10s)
  ├─ DynamoDB: processed-updates (dedup)
  └─ SQS: updates queue
        │
        ▼
ProcessorFunction (Lambda, 300s)
  ├─ DynamoDB: conversations (history)
  ├─ DynamoDB: subscribers (/subscribe)
  ├─ Gemini API (tool-use loop)
  │     └─ Finnhub API (earnings / estimates / news / quote)
  └─ Telegram Bot API (回覆)

EventBridge (daily cron)
   ▼
SchedulerFunction (Lambda, 300s)
  ├─ DynamoDB: subscribers
  ├─ Finnhub API
  └─ Telegram Bot API (推播)
```

每一層都對應到 `infra/template.yaml` 的一個 resource。

---

## 2. 技術分類與職責

### Runtime & 語言
| 技術 | 用途 |
|------|------|
| Python 3.11+ | 主要語言（`requires-python = ">=3.11"`） |
| **uv** | dependency 與 virtualenv 管理（取代 pip + venv），`make install` 跑 `uv sync` |
| **hatchling** | build backend，把 `src/agent/` 打包成 wheel |

### AI / Agent 層
| 技術 | 用途 |
|------|------|
| **Google Gemini API** (`gemini-2.5-flash`) | 核心 LLM，負責理解用戶訊息、決定要不要呼叫工具、產生繁體中文回答 |
| **`google-genai` SDK** | 呼叫 Gemini，支援 function calling 與多輪 contents |
| 自訂 **tool-use loop** | `src/agent/agent.py` 的 `run_agent()`，最多 10 圈，每圈解析 Gemini 的 `function_call`、執行工具、把結果塞回 `contents`，直到 Gemini 給出純文字答案 |

### 外部資料來源
| 技術 | 用途 |
|------|------|
| **Finnhub API** | 美股資料源；4 個 tool 全部打它：earnings calendar、analyst estimates、stock news、real-time quote |
| **`httpx`** | 同步 HTTP client，被 Finnhub tools 與 `telegram_client.py` 共用 |

### Messaging / 用戶介面
| 技術 | 用途 |
|------|------|
| **Telegram Bot API** | 用戶入口；採 webhook 模式（不是 long polling），所以需要公開 HTTPS endpoint |
| `telegram_client.py` | 自製 wrapper，處理 >4000 字元自動分段、Markdown parse 失敗 fallback 為純文字 |

### AWS Serverless 基礎建設（透過 SAM 部署）
| 技術 | 用途 |
|------|------|
| **API Gateway HTTP API** | 接收 Telegram webhook POST，路由到 `WebhookFunction` |
| **Lambda × 3** | 三個 handler 共用同一 Docker image，靠 `ImageConfig.Command` 切換進入點：<br>• `webhook_handler`（10s）— dedup + 推 SQS<br>• `processor_handler`（300s, 1024MB）— 跑 agent loop<br>• `scheduler_handler`（300s）— 每日推播 |
| **SQS + DLQ** | 解耦 webhook 與 agent。webhook 必須在 60s 內回 200，但 agent loop 可能跑幾十秒；DLQ 設 3 次失敗後接管，保留 14 天 |
| **EventBridge ScheduleV2** | 每日 UTC 00:00 觸發 `SchedulerFunction` |
| **DynamoDB × 3** | 全部 PAY_PER_REQUEST：<br>• `conversations`（PK `chat_id` + SK `timestamp`，30 天 TTL）<br>• `subscribers`（PK `chat_id`）<br>• `processed-updates`（PK `update_id`，1 小時 TTL，做 Telegram update dedup） |
| **SSM Parameter Store** | production 機密管理；所有 API key 放 `/earnings-agent/*` 之下，啟動時透過 `ssm:GetParameters` 拉取 |
| **KMS** | 解密 SSM SecureString（policy 裡帶 `kms:Decrypt` on `alias/aws/ssm`） |
| **ECR + Docker** | Lambda 採 container image 部署；SAM build 時把 image 推到 ECR |

### AWS SDK / Lambda 工具
| 技術 | 用途 |
|------|------|
| **`boto3`** | DynamoDB 與 SQS 存取；用 resource API + `@lru_cache` 確保整個 Lambda 容器內 table / queue 物件只建立一次 |
| **`aws-lambda-powertools`** | structured logging（`POWERTOOLS_SERVICE_NAME=earnings-agent`），方便在 CloudWatch 撈 log |

### Local 開發
| 技術 | 用途 |
|------|------|
| **FastAPI + uvicorn** | `local_server.py` 模擬 webhook → processor 同步流程，不需要真的起 SQS / Lambda；`make dev` 跑在 :8080 |
| **.env** | 本地 config fallback，當 SSM 不可用時讀環境變數（`config.py` 裡 `@lru_cache` 統一處理） |

### 測試
| 技術 | 用途 |
|------|------|
| **pytest** | 測試 framework，`PYTHONPATH=src uv run pytest tests/` |
| **`pytest-mock`** | mock Gemini API 與 Finnhub HTTP 呼叫 |
| **`moto`** | mock DynamoDB / SQS；`tests/conftest.py` 自動建表、建 queue |
| 原則 | 測試永遠不打真 AWS 或真外部 API |

### 部署 / 開發工具鏈
| 技術 | 用途 |
|------|------|
| **AWS SAM CLI** | `make build` → `sam build`、`make deploy` → `sam deploy`，把 `infra/template.yaml` 轉成 CloudFormation stack |
| **Docker** | 三個 Lambda 共用 `Dockerfile`，build 出單一 image |
| **Makefile** | 統一 install / test / dev / build / deploy / logs-* 命令 |

---

## 3. 關鍵設計決策

**為什麼用 SQS 解耦 webhook 與 processor？**
Telegram 要求 webhook 在 60 秒內回 200，但 Gemini tool-use loop（最多 10 圈，每圈一次 API call + 可能的 Finnhub 呼叫）可能跑幾十秒。把訊息丟進 SQS 後立即回 200，讓 ProcessorFunction 慢慢跑，最長給到 300 秒。

**為什麼三個 Lambda 共用同一個 Docker image？**
build 一次推一次，三個 function 只是 `ImageConfig.Command` 不同（`agent.webhook_handler.handler` / `agent.processor_handler.handler` / `agent.scheduler_handler.handler`），維護成本最低。

**為什麼 DynamoDB 用 on-demand + TTL？**
流量低（個人專案級），on-demand 不用估 capacity；TTL 自動清理過期對話與 dedup 紀錄，省手動 cleanup，整體月成本 $0。

**為什麼用 SSM 而不是 Lambda 環境變數放機密？**
SSM 可以在不重部署的情況下輪換 API key；環境變數一旦寫進 template，輪換就要 redeploy。SSM 也讓三個 Lambda 共用同一份機密來源。

**為什麼 Gemini 工具結果要包成 dict？**
Gemini 的 `FunctionResponse` schema 規定 response 必須是 dict，但有些 tool 自然回傳 list / str。`_run_tool()` 統一把非 dict 結果包成 `{"result": ...}`，新工具不用為了 SDK 限制改回傳型別。

---

## 4. 成本概要

| 元件 | 月費用 |
|------|--------|
| API Gateway + Lambda + EventBridge + DynamoDB + SQS + ECR | 免費額度內 ~$0 |
| Gemini API (`gemini-2.5-flash`) | ~$1–5 |
| Finnhub API（free tier）+ Telegram Bot | $0 |
| **合計** | **~$1–5/月** |

---

## 5. Repo 對照表

| 模組路徑 | 用到的技術 |
|---------|----------|
| `src/agent/agent.py` | google-genai, Gemini tool use loop |
| `src/agent/tools/*` | httpx, Finnhub API |
| `src/agent/db/*` | boto3, DynamoDB |
| `src/agent/telegram_client.py` | httpx, Telegram Bot API |
| `src/agent/webhook_handler.py` | aws-lambda-powertools, boto3 (SQS + DynamoDB) |
| `src/agent/processor_handler.py` | aws-lambda-powertools, agent loop |
| `src/agent/scheduler_handler.py` | aws-lambda-powertools, EventBridge trigger |
| `src/agent/config.py` | boto3 SSM, `@lru_cache` |
| `src/agent/local_server.py` | FastAPI, uvicorn |
| `infra/template.yaml` | AWS SAM, CloudFormation |
| `Dockerfile` | Docker, ECR |
| `tests/` | pytest, pytest-mock, moto |
| `pyproject.toml` | uv, hatchling |
| `Makefile` | 統一命令入口 |
