# Stock Earnings AI Agent — Build Plan

## 專案目標

建立一個以 Telegram 為介面的 AI Agent，具備：
1. **主動提醒**：美股 earnings call 發布前一天通知，附市場預期與分析師報告摘要
2. **新聞彙整**：抓取股票相關新聞與企業報告，提供繁體中文翻譯
3. **對話互動**：用戶可用自然語言查詢，Agent 自動決定呼叫哪些工具
4. **對話紀錄持久化**：所有對話存入 DynamoDB，重啟不遺失

---

## 架構概覽

```
用戶
 ↕ Telegram
Telegram Webhook
 ↓
API Gateway（HTTP API）
 ↓
Lambda：webhook_handler（10s timeout）
 ↓ 驗證 + dedup → 推入 SQS
SQS Queue（+ Dead Letter Queue）
 ↓
Lambda：processor_handler（300s timeout）
 ↓
AI Agent（Gemini gemini-2.5-flash + Tool Use）
 ├─ Tool: get_earnings_calendar     # 取得 earnings 行事曆
 ├─ Tool: get_analyst_estimates     # 分析師 EPS / 營收預期
 └─ Tool: get_stock_news            # 抓取最新新聞（Gemini 直接翻譯）
 ↓
DynamoDB
 ├─ Table: conversations            # 對話紀錄（含 TTL）
 ├─ Table: subscribers              # 訂閱者清單
 └─ Table: processed-updates        # Telegram update dedup（含 TTL）

背景提醒：
EventBridge Scheduler（每天 08:00 台灣時間）
 ↓
Lambda：scheduler_handler           # 掃描隔天 earnings → 主動推播
```

---

## AWS 技術選型

| 元件 | AWS 服務 | 費用 |
|---|---|---|
| Webhook 接收 | API Gateway (HTTP API) + Lambda | 免費額度內 ~$0 |
| 每日排程提醒 | EventBridge Scheduler + Lambda | ~$0 |
| 對話紀錄 / 訂閱者 | DynamoDB (On-Demand) | 免費額度內 ~$0 |
| 密鑰管理 | SSM Parameter Store (Standard) | ~$0 |
| 容器部署 | ECR + Lambda Container Image | ~$0 |
| 訊息佇列 | SQS + DLQ | 免費額度內 ~$0 |
| AI Agent | Gemini API (`gemini-2.5-flash`) | 預估 $1-5/月 |
| 股市資料 / 新聞 | Finnhub API 免費方案 | $0 |
| 介面 | Telegram Bot | $0 |
| **合計** | | **~$1-5/月（幾乎全是 Gemini API）** |

---

## 專案結構

```
stock-agent/
├── src/
│   └── agent/
│       ├── __init__.py
│       ├── webhook_handler.py     # Lambda handler：驗證 + dedup + 推入 SQS
│       ├── processor_handler.py   # Lambda handler：從 SQS 消費，執行 Agent
│       ├── scheduler_handler.py   # Lambda handler：每日排程提醒
│       ├── agent.py               # Gemini Tool Use 核心迴圈
│       ├── local_server.py        # FastAPI 本地開發伺服器
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── _http.py           # 共用 httpx client（Finnhub base URL）
│       │   ├── earnings.py        # get_earnings_calendar
│       │   ├── estimates.py       # get_analyst_estimates
│       │   └── news.py            # get_stock_news
│       ├── db/
│       │   ├── __init__.py
│       │   ├── conversations.py   # DynamoDB 對話紀錄 CRUD
│       │   ├── dedup.py           # Telegram update_id 去重（conditional put）
│       │   └── subscribers.py     # DynamoDB 訂閱者 CRUD
│       ├── telegram_client.py     # Telegram Bot API 封裝
│       └── config.py              # 從 SSM Parameter Store 讀取設定
├── infra/
│   └── template.yaml              # SAM template（3 Lambda + API Gateway + SQS + EventBridge）
├── tests/
│   ├── conftest.py                # 共用 fixtures：moto mock AWS + 環境變數
│   ├── test_agent.py
│   ├── test_tools.py
│   ├── test_db.py
│   └── test_webhook.py
├── docs/
│   └── deployment-flow.md         # 部署與請求流程圖（Mermaid）
├── pyproject.toml
├── .env.example                   # 本地開發用
├── Dockerfile                     # Lambda Container Image
├── Makefile                       # 常用指令捷徑
└── README.md
```

---

## DynamoDB Schema

### Table: `earnings-agent-conversations`

| 欄位 | 型別 | 說明 |
|---|---|---|
| `chat_id` (PK) | String | Telegram chat ID |
| `timestamp` (SK) | String | ISO 8601 timestamp |
| `role` | String | `user` 或 `assistant` |
| `content` | String | 訊息內容 |
| `ttl` | Number | Unix timestamp，設 30 天後自動刪除 |

GSI 不需要，查詢模式只有：
- 取得某 `chat_id` 最近 N 筆 → Query PK + SortKey DESC + Limit

### Table: `earnings-agent-subscribers`

| 欄位 | 型別 | 說明 |
|---|---|---|
| `chat_id` (PK) | String | Telegram chat ID |
| `subscribed_at` | String | ISO 8601 timestamp |
| `active` | Boolean | 是否啟用提醒 |

### Table: `earnings-agent-processed-updates`

| 欄位 | 型別 | 說明 |
|---|---|---|
| `update_id` (PK) | Number | Telegram update ID |
| `ttl` | Number | Unix timestamp，1 小時後自動刪除 |

用途：Telegram Webhook 重試時透過 conditional put 進行去重，避免重複處理同一則訊息。

---

## SSM Parameter Store 設定

路徑統一放在 `/earnings-agent/` 前綴：

```
/earnings-agent/finnhub-api-key        → Finnhub API Key（SecureString）
/earnings-agent/gemini-api-key         → Google Gemini API Key（SecureString）
/earnings-agent/telegram-bot-token     → Telegram Bot Token（SecureString）
/earnings-agent/telegram-webhook-secret → Telegram Webhook 驗證密鑰（SecureString）
```

Lambda IAM Role 需要 `ssm:GetParameters` 權限（僅限此路徑）。

---

## 實作步驟

### Step 1：初始化專案

```bash
mkdir stock-agent && cd stock-agent
uv init
uv add google-genai httpx boto3 aws-lambda-powertools

# 開發工具
uv add --dev pytest moto boto3 moto[dynamodb,sqs]
```

### Step 2：實作 config.py（SSM 參數載入）

```python
import os
from functools import lru_cache
import boto3

@lru_cache(maxsize=1)
def _load_secrets() -> dict:
    if not os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        return {}
    ssm = boto3.client("ssm", region_name=aws_region())
    resp = ssm.get_parameters(
        Names=[
            "/earnings-agent/finnhub-api-key",
            "/earnings-agent/gemini-api-key",
            "/earnings-agent/telegram-bot-token",
            "/earnings-agent/telegram-webhook-secret",
        ],
        WithDecryption=True,
    )
    return {p["Name"].split("/")[-1]: p["Value"] for p in resp["Parameters"]}

def finnhub_api_key() -> str:
    return _load_secrets().get("finnhub-api-key") or os.environ["FINNHUB_API_KEY"]

def gemini_api_key() -> str:
    return _load_secrets().get("gemini-api-key") or os.environ["GEMINI_API_KEY"]
```

### Step 3：實作 db/conversations.py（對話紀錄 CRUD）

功能：
- `save_message(chat_id, role, content)` — 寫入一筆訊息，TTL = 現在 + 30 天
- `get_history(chat_id, limit=20)` — 取得最近 N 筆，按時間排序
- `build_messages(chat_id)` — 回傳 `list[dict]` 供 agent 使用

注意事項：
- Query 時用 `ScanIndexForward=False` + `Limit` 取最新的，再 reverse 排序給 Gemini
- 對話歷史最多保留 20 筆（10 輪），避免 context 過長

### Step 4：實作 db/subscribers.py（訂閱者 CRUD）

功能：
- `subscribe(chat_id)` — 新增或更新，`active=True`
- `unsubscribe(chat_id)` — 設定 `active=False`
- `get_all_active()` — Scan 取得所有 `active=True` 的 chat_id

### Step 5：定義 Gemini Tools（tools/ 資料夾）

每個 tool 包含兩個部分：
- **Tool schema**：JSON schema，告訴 Gemini 工具名稱、功能描述、參數
- **Tool function**：實際呼叫 Finnhub API 的函式

#### tools/earnings.py
- Tool name: `get_earnings_calendar`
- 參數: `date_from: str (YYYY-MM-DD)`, `date_to: str (YYYY-MM-DD)`
- Finnhub endpoint: `GET /calendar/earnings?from={}&to={}&token={}`
- 回傳: `list[dict]`，每筆含 symbol, date, epsEstimate, revenueEstimate, hour
- 結果上限 100 筆，避免 token 爆量

#### tools/estimates.py
- Tool name: `get_analyst_estimates`
- 參數: `symbol: str`
- 呼叫兩支 Finnhub API：
  - `GET /stock/recommendation` → 買/賣/持有評級分佈
  - `GET /stock/eps-estimate` → 未來 EPS 預估
- 回傳: 合併後的分析師共識 dict

#### tools/news.py
- Tool name: `get_stock_news`
- 參數: `symbol: str`, `days_back: int`（預設 7，最大 30）
- Finnhub endpoint: `GET /company-news?symbol={}&from={}&to={}&token={}`
- 回傳: 最新 5 則新聞（headline, url, summary, datetime）
- 只回傳原文，翻譯由 Gemini 回應時自行處理

### Step 6：實作 agent.py（Gemini Tool Use 核心迴圈）

```python
from google import genai
from google.genai import types

MODEL = "gemini-2.5-flash"
SYSTEM_INSTRUCTION = """你是一個專業的美股財報分析助理。..."""

def run_agent(user_message: str, history: list[dict]) -> str:
    """
    Gemini Tool Use agentic loop（最多 10 輪）：
    1. 傳入 user message + 對話歷史（已從 DynamoDB 讀取）
    2. Gemini 決定是否呼叫 tool（function_call）
    3. 若有 function_call → 執行對應函式 → 以 FunctionResponse 回傳結果
    4. 重複直到回傳 text（非 function_call）
    注意：FunctionResponse.response 必須是 dict，list 需包裝為 {"result": list}
    """
    contents = _build_history_contents(history)
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=user_message)]))

    for _ in range(MAX_TOOL_ROUNDS):
        resp = _client().models.generate_content(
            model=MODEL, contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=_build_tools(),
            ),
        )
        part = resp.candidates[0].content.parts[0]
        if part.function_call is None:
            return part.text

        # 執行 tool，將結果以 FunctionResponse 回傳
        tool_result = _run_tool(part.function_call.name, dict(part.function_call.args))
        contents.append(resp.candidates[0].content)
        contents.append(types.Content(
            role="user",
            parts=[types.Part.from_function_response(
                name=part.function_call.name, response=tool_result,
            )],
        ))
    return contents[-1].parts[0].text
```

### Step 7：實作 webhook_handler.py（Lambda — 驗證 + 推入 SQS）

```python
def handler(event, context):
    """
    API Gateway → Lambda（10s timeout）
    1. 驗證 Telegram webhook secret
    2. 解析 update，用 dedup.claim_update_id() 去重
    3. 推入 SQS queue
    4. 立即回傳 200（不等 Agent 處理完）
    """
    body = json.loads(event["body"])
    update_id = body["update_id"]

    if not dedup.claim_update_id(update_id):
        return {"statusCode": 200}  # 重複 update，跳過

    _sqs().send_message(QueueUrl=queue_url, MessageBody=json.dumps(body))
    return {"statusCode": 200}
```

### Step 8：實作 processor_handler.py（Lambda — 從 SQS 消費）

```python
def handler(event, context):
    """
    SQS → Lambda（300s timeout）
    1. 從 SQS event 取出 Telegram update
    2. 處理 /subscribe、/unsubscribe 等指令
    3. 一般訊息：讀取對話歷史 → 執行 run_agent() → 回覆 Telegram
    4. 儲存對話紀錄到 DynamoDB
    """
    for record in event["Records"]:
        body = json.loads(record["body"])
        message = body.get("message", {})
        chat_id = str(message["chat"]["id"])
        text = message.get("text", "")

        history = build_messages(chat_id)
        reply = run_agent(text, history)

        save_message(chat_id, "user", text)
        save_message(chat_id, "assistant", reply)
        send_message(chat_id, reply)
```

### Step 9：實作 scheduler_handler.py（每日提醒 Lambda）

```python
def handler(event, context):
    """
    EventBridge Scheduler 每天 08:00（台灣時間）觸發
    """
    tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    earnings = get_earnings_calendar(tomorrow, tomorrow)

    if not earnings:
        return {"statusCode": 200}

    prompt = f"明天（{tomorrow}）有以下公司公布財報：{earnings}\n..."
    summary = run_agent(prompt, [])

    subscribers = get_all_active()
    for chat_id in subscribers:
        send_message(chat_id, summary)

    return {"statusCode": 200}
```

### Step 10：telegram_client.py

- `send_message(chat_id, text, parse_mode="Markdown")`
- 訊息超過 4096 字元時自動切割分段發送
- 使用 `httpx` 同步模式（Lambda 環境較單純）

### Step 11：SAM Template（infra/template.yaml）

定義以下資源：
- `WebhookFunction`：Lambda + API Gateway HTTP API，記憶體 512MB，timeout 10s
- `ProcessorFunction`：Lambda（SQS 觸發），timeout 300s（給 Gemini 足夠時間）
- `SchedulerFunction`：Lambda，timeout 300s
- `UpdatesQueue`：SQS Queue + Dead Letter Queue
- `SchedulerRule`：EventBridge Scheduler，cron `0 0 * * ? *`（UTC 00:00 = 台灣 08:00）
- `ConversationsTable`：DynamoDB，On-Demand 計費，TTL 欄位 `ttl`
- `SubscribersTable`：DynamoDB，On-Demand 計費
- `ProcessedUpdatesTable`：DynamoDB，On-Demand 計費，TTL 欄位 `ttl`
- `LambdaExecutionRole`：IAM Role，賦予 DynamoDB + SSM + SQS + CloudWatch Logs 權限

### Step 12：Dockerfile（Lambda Container Image）

```dockerfile
FROM public.ecr.aws/lambda/python:3.11
WORKDIR ${LAMBDA_TASK_ROOT}

COPY pyproject.toml .
RUN pip install uv && uv export --no-dev > requirements.txt && pip install -r requirements.txt

COPY src/agent/ ./agent/

CMD ["agent.webhook_handler.handler"]
```

### Step 13：Makefile（開發常用指令）

```makefile
install:
    uv sync

test:
    PYTHONPATH=src uv run pytest tests/ -v

dev:
    PYTHONPATH=src uv run uvicorn agent.local_server:app --reload --port 8080

build:
    cd infra && sam build

deploy:
    cd infra && sam deploy --resolve-s3

logs-webhook:
    cd infra && sam logs -n WebhookFunction --tail

logs-processor:
    cd infra && sam logs -n ProcessorFunction --tail

logs-scheduler:
    cd infra && sam logs -n SchedulerFunction --tail
```

### Step 14：本地開發模擬

Lambda 沒辦法直接本地跑 webhook，建立 `agent/local_server.py`：
- 用 FastAPI 包裝 `webhook_handler.handler`
- 用 [ngrok](https://ngrok.com) 將本地 port expose 成 HTTPS URL
- 設定 Telegram Webhook 指向 ngrok URL 即可本地測試

---

## 部署流程

```bash
# 1. 設定 SSM 參數（只需做一次）
aws ssm put-parameter --name "/earnings-agent/finnhub-api-key" \
  --value "your_key" --type SecureString

aws ssm put-parameter --name "/earnings-agent/gemini-api-key" \
  --value "your_key" --type SecureString

aws ssm put-parameter --name "/earnings-agent/telegram-bot-token" \
  --value "your_token" --type SecureString

aws ssm put-parameter --name "/earnings-agent/telegram-webhook-secret" \
  --value "your_secret" --type SecureString

# 2. Build & Deploy
make build && make deploy  # 第一次加 GUIDED=1

# 3. 設定 Telegram Webhook（取得 API Gateway URL 後執行）
curl "https://api.telegram.org/bot{TOKEN}/setWebhook?url={API_GATEWAY_URL}/webhook&secret_token={SECRET}"
```

---

## 環境變數取得方式

| 變數 | 取得方式 |
|---|---|
| `FINNHUB_API_KEY` | 註冊 https://finnhub.io |
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| `TELEGRAM_BOT_TOKEN` | Telegram 搜尋 @BotFather → `/newbot` |
| `TELEGRAM_WEBHOOK_SECRET` | 自訂密鑰，用於驗證 Telegram webhook 來源 |

取得 Telegram `chat_id`：
1. 對 Bot 傳任意訊息
2. 呼叫 `https://api.telegram.org/bot{TOKEN}/getUpdates`
3. 從 response 取得 `message.chat.id`

---

## 對話範例

```
用戶：明天有哪些科技股要公布財報？

Agent：[→ get_earnings_calendar(明天, 明天)]

       📊 *明日科技股財報*

       *NVDA（NVIDIA）*
       - 財報時間：盤後（After Market）
       - EPS 預期：$0.89（去年同期 $0.82）
       - 營收預期：$24.6B
       - 分析師評級：買入 28 / 持有 5 / 賣出 1

---

用戶：幫我看一下 AAPL 最近的新聞

Agent：[→ get_stock_news("AAPL", days_back=7)]

       📰 *Apple 最新消息（近 7 天）*

       1. *Apple 推出新款 MacBook Pro*
          蘋果公司本週發布搭載 M4 晶片的新款...

       2. *分析師上調 Apple 目標價至 $215*
          高盛分析師認為 iPhone 16 週期...
```

---

## 完成標準

- [x] Telegram 傳訊息，Agent 能理解並以繁體中文回應
- [x] 詢問特定股票新聞，回傳中文翻譯摘要
- [x] 詢問分析師預期，回傳清楚格式
- [x] 詢問 earnings 行事曆，回傳正確結果
- [ ] 每天早上 8:00 主動推播隔天財報提醒
- [ ] `/subscribe` 與 `/unsubscribe` 指令正常運作
- [x] 多輪對話正常（對話歷史從 DynamoDB 讀取，重啟不遺失）
- [x] DynamoDB TTL 正常運作（30 天後自動清除舊對話）
- [x] SQS 解耦架構正常（webhook → SQS → processor）
- [x] Telegram update dedup 正常（避免重複處理）
- [x] SAM deploy 成功，Lambda + API Gateway + SQS + EventBridge 全部正常

---

## Phase 2 預留擴充點（留 TODO 註解，不實作）

- [ ] 用戶自訂 watchlist 指令（`/watch AAPL`、`/unwatch AAPL`、`/mylist`）
- [x] ~~Webhook handler 改為非同步：Lambda 先回 200，再用 SQS 處理 Agent（避免 Telegram timeout）~~ ✅ 已實作
- [ ] 加入股價即時查詢 tool（`get_quote`）
- [ ] 支援財報電話會議逐字稿摘要（Finnhub earnings transcript endpoint）
- [ ] 加入技術指標 tool（RSI、MA、布林通道）
- [ ] CloudWatch Dashboard 監控 Lambda 錯誤率與 Gemini API 費用
