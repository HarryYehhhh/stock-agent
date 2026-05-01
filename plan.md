# Stock Earnings AI Agent — Build Plan

## 專案目標

建立一個以 Telegram 為介面的 AI Agent，具備：
1. **主動提醒**：美股 earnings call 發布前一天通知，附市場預期與分析師報告摘要
2. **產業篩選**：根據產業類別（sector）抓取相關股票
3. **新聞彙整**：抓取股票相關新聞與企業報告，提供繁體中文翻譯
4. **對話互動**：用戶可用自然語言查詢，Agent 自動決定呼叫哪些工具
5. **對話紀錄持久化**：所有對話存入 DynamoDB，重啟不遺失

---

## 架構概覽

```
用戶
 ↕ Telegram
Telegram Webhook
 ↓
API Gateway（HTTP API）
 ↓
Lambda：webhook_handler
 ↓
AI Agent（Claude claude-sonnet-4-20250514 + Tool Use）
 ├─ Tool: get_earnings_calendar     # 取得 earnings 行事曆
 ├─ Tool: get_analyst_estimates     # 分析師 EPS / 營收預期
 ├─ Tool: get_stocks_by_sector      # 依產業類別篩選股票
 └─ Tool: get_stock_news            # 抓取最新新聞（Claude 直接翻譯）
 ↓
DynamoDB
 ├─ Table: conversations            # 對話紀錄（含 TTL）
 └─ Table: subscribers             # 訂閱者清單

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
| AI Agent | Claude API (`claude-sonnet-4-20250514`) | 預估 $3-10/月 |
| 股市資料 / 新聞 | Finnhub API 免費方案 | $0 |
| 介面 | Telegram Bot | $0 |
| **合計** | | **~$3-10/月（幾乎全是 Claude API）** |

---

## 專案結構

```
earnings-agent/
├── src/
│   └── agent/
│       ├── __init__.py
│       ├── webhook_handler.py     # Lambda handler：處理 Telegram Webhook
│       ├── scheduler_handler.py   # Lambda handler：每日排程提醒
│       ├── agent.py               # Claude Tool Use 核心迴圈
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── earnings.py        # get_earnings_calendar
│       │   ├── estimates.py       # get_analyst_estimates
│       │   ├── sector.py          # get_stocks_by_sector
│       │   └── news.py            # get_stock_news
│       ├── db/
│       │   ├── __init__.py
│       │   ├── conversations.py   # DynamoDB 對話紀錄 CRUD
│       │   └── subscribers.py     # DynamoDB 訂閱者 CRUD
│       ├── telegram_client.py     # Telegram Bot API 封裝
│       └── config.py              # 從 SSM Parameter Store 讀取設定
├── config/
│   └── sector_symbols.json        # 各產業股票清單
├── infra/
│   ├── template.yaml              # SAM template（Lambda + API Gateway + EventBridge）
│   └── dynamodb.yaml              # DynamoDB table 定義
├── tests/
│   ├── test_agent.py
│   ├── test_tools.py
│   └── test_db.py
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

---

## SSM Parameter Store 設定

路徑統一放在 `/earnings-agent/` 前綴：

```
/earnings-agent/finnhub-api-key        → Finnhub API Key（SecureString）
/earnings-agent/anthropic-api-key      → Anthropic API Key（SecureString）
/earnings-agent/telegram-bot-token     → Telegram Bot Token（SecureString）
/earnings-agent/telegram-webhook-url   → 部署後的 API Gateway URL（String）
```

Lambda IAM Role 需要 `ssm:GetParameters` 權限（僅限此路徑）。

---

## 實作步驟

### Step 1：初始化專案

```bash
mkdir earnings-agent && cd earnings-agent
uv init
uv add anthropic httpx boto3 aws-lambda-powertools

# 開發工具
uv add --dev pytest pytest-asyncio moto aws-sam-cli
```

### Step 2：建立 config/sector_symbols.json

```json
{
  "Technology": ["AAPL","MSFT","NVDA","META","GOOGL","AMZN","TSLA","AMD","INTC","CRM"],
  "Healthcare": ["JNJ","UNH","PFE","ABBV","MRK","TMO","ABT","DHR","BMY","AMGN"],
  "Finance":    ["JPM","BAC","WFC","GS","MS","BLK","C","AXP","SCHW","USB"],
  "Energy":     ["XOM","CVX","COP","SLB","EOG","MPC","PSX","VLO","OXY","KMI"],
  "Consumer":   ["WMT","COST","PG","KO","PEP","MCD","SBUX","NKE","TGT","HD"]
}
```

### Step 3：實作 config.py（SSM 參數載入）

```python
import boto3
from functools import lru_cache

@lru_cache(maxsize=None)
def get_config() -> dict:
    """
    從 SSM Parameter Store 批次讀取所有設定。
    lru_cache 確保同一 Lambda 執行環境只讀一次（利用 warm start）。
    本地開發時，從環境變數讀取（.env 檔）。
    """
    ssm = boto3.client("ssm", region_name="ap-northeast-1")
    
    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        # 在 Lambda 環境中，從 SSM 讀取
        params = ssm.get_parameters(
            Names=[
                "/earnings-agent/finnhub-api-key",
                "/earnings-agent/anthropic-api-key",
                "/earnings-agent/telegram-bot-token",
            ],
            WithDecryption=True,
        )
        return {p["Name"].split("/")[-1]: p["Value"] for p in params["Parameters"]}
    else:
        # 本地開發，從環境變數讀取
        return {
            "finnhub-api-key": os.environ["FINNHUB_API_KEY"],
            "anthropic-api-key": os.environ["ANTHROPIC_API_KEY"],
            "telegram-bot-token": os.environ["TELEGRAM_BOT_TOKEN"],
        }
```

### Step 4：實作 db/conversations.py（對話紀錄 CRUD）

功能：
- `save_message(chat_id, role, content)` — 寫入一筆訊息，TTL = 現在 + 30 天
- `get_history(chat_id, limit=20)` — 取得最近 N 筆，按時間排序
- `build_messages_for_claude(chat_id)` — 回傳符合 Claude API 格式的 `list[dict]`

注意事項：
- Query 時用 `ScanIndexForward=False` + `Limit` 取最新的，再 reverse 排序給 Claude
- 對話歷史最多保留 20 筆（10 輪），避免 Claude context 過長

### Step 5：實作 db/subscribers.py（訂閱者 CRUD）

功能：
- `subscribe(chat_id)` — 新增或更新，`active=True`
- `unsubscribe(chat_id)` — 設定 `active=False`
- `get_all_active()` — Scan 取得所有 `active=True` 的 chat_id

### Step 6：定義 Claude Tools（tools/ 資料夾）

每個 tool 包含兩個部分：
- **Tool schema**：JSON schema，告訴 Claude 工具名稱、功能描述、參數
- **Tool function**：實際呼叫 Finnhub API 的非同步函式

#### tools/earnings.py
- Tool name: `get_earnings_calendar`
- 參數: `date_from: str (YYYY-MM-DD)`, `date_to: str (YYYY-MM-DD)`
- Finnhub endpoint: `GET /calendar/earnings?from={}&to={}&token={}`
- 回傳: `list[dict]`，每筆含 symbol, date, epsEstimate, revenueEstimate, hour

#### tools/estimates.py
- Tool name: `get_analyst_estimates`
- 參數: `symbol: str`
- 呼叫兩支 Finnhub API：
  - `GET /stock/recommendation` → 買/賣/持有評級分佈
  - `GET /stock/eps-estimate` → 未來 EPS 預估
- 回傳: 合併後的分析師共識 dict

#### tools/sector.py
- Tool name: `get_stocks_by_sector`
- 參數: `sector: str`（Technology / Healthcare / Finance / Energy / Consumer）
- 讀取 `config/sector_symbols.json`，回傳該產業的股票列表
- 若 sector 不在清單中，回傳所有可用產業名稱

#### tools/news.py
- Tool name: `get_stock_news`
- 參數: `symbol: str`, `days_back: int`（預設 7，最大 30）
- Finnhub endpoint: `GET /company-news?symbol={}&from={}&to={}&token={}`
- 回傳: 最新 5 則新聞（headline, url, summary, datetime）
- 只回傳原文，翻譯由 Claude 回應時自行處理

### Step 7：實作 agent.py（Tool Use 核心迴圈）

```python
SYSTEM_PROMPT = """
你是一個專業的美股財報分析助理。
- 一律使用繁體中文回應
- 所有英文新聞標題與摘要，主動翻譯成繁體中文再呈現
- 回應格式使用 Telegram Markdown（粗體用 *，程式碼用 `）
- 財報數字格式：EPS 用美元（$），營收用 B/M 縮寫
- 分析師評級：顯示買入/持有/賣出數量分佈
"""

async def run_agent(user_message: str, history: list[dict]) -> str:
    """
    Claude Tool Use agentic loop：
    1. 傳入 user message + 對話歷史（已從 DynamoDB 讀取）
    2. Claude 決定是否呼叫 tool
    3. 若有 tool_use → 執行對應函式 → 結果回傳給 Claude
    4. 重複直到 stop_reason == "end_turn"
    """
    messages = history + [{"role": "user", "content": user_message}]

    while True:
        response = anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            tools=ALL_TOOL_SCHEMAS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            return "".join(b.text for b in response.content if b.type == "text")

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    fn = TOOL_FUNCTIONS[block.name]
                    result = await fn(**block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                    })
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
```

### Step 8：實作 webhook_handler.py（Lambda Entry Point）

```python
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger()

@logger.inject_lambda_context
def handler(event: dict, context: LambdaContext) -> dict:
    """
    API Gateway HTTP API → Lambda Proxy Integration
    Telegram 傳來的 update 格式在 event["body"]
    必須在 3 秒內回傳 200（Telegram timeout），
    實際 Agent 處理可在回傳後非同步繼續（若超時考慮改 SQS）
    """
    body = json.loads(event["body"])
    message = body.get("message", {})
    chat_id = str(message["chat"]["id"])
    text = message.get("text", "")

    # 指令處理
    if text == "/subscribe":
        subscribe(chat_id)
        send_message(chat_id, "✅ 已訂閱每日財報提醒")
        return {"statusCode": 200}

    if text == "/unsubscribe":
        unsubscribe(chat_id)
        send_message(chat_id, "❌ 已取消訂閱")
        return {"statusCode": 200}

    # 從 DynamoDB 讀取對話歷史
    history = build_messages_for_claude(chat_id)

    # 執行 Agent
    reply = asyncio.run(run_agent(text, history))

    # 儲存這輪對話到 DynamoDB
    save_message(chat_id, "user", text)
    save_message(chat_id, "assistant", reply)

    # 回傳給用戶
    send_message(chat_id, reply)

    return {"statusCode": 200}
```

### Step 9：實作 scheduler_handler.py（每日提醒 Lambda）

```python
def handler(event: dict, context) -> dict:
    """
    EventBridge Scheduler 每天 08:00（台灣時間）觸發
    """
    tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    earnings = asyncio.run(get_earnings_calendar(tomorrow, tomorrow))

    if not earnings:
        logger.info("明天無財報，跳過推播")
        return {"statusCode": 200}

    prompt = (
        f"明天（{tomorrow}）有以下公司公布財報：{earnings}\n"
        "請用繁體中文條列每家公司的財報時間、分析師 EPS 預期、"
        "營收預期，並附上整體市場注意事項。"
    )
    summary = asyncio.run(run_agent(prompt, []))

    subscribers = get_all_active()
    for chat_id in subscribers:
        send_message(chat_id, f"📊 *明日財報提醒*\n\n{summary}")

    return {"statusCode": 200}
```

### Step 10：telegram_client.py

- `send_message(chat_id, text, parse_mode="Markdown")`
- 訊息超過 4096 字元時自動切割分段發送
- 使用 `httpx` 同步模式（Lambda 環境較單純）

### Step 11：SAM Template（infra/template.yaml）

定義以下資源：
- `WebhookFunction`：Lambda + API Gateway HTTP API，記憶體 512MB，timeout 29s
- `SchedulerFunction`：Lambda，timeout 300s（給 Claude 足夠時間）
- `SchedulerRule`：EventBridge Scheduler，cron `0 0 * * ? *`（UTC 00:00 = 台灣 08:00）
- `ConversationsTable`：DynamoDB，On-Demand 計費，TTL 欄位 `ttl`
- `SubscribersTable`：DynamoDB，On-Demand 計費
- `LambdaExecutionRole`：IAM Role，賦予 DynamoDB + SSM + CloudWatch Logs 權限

### Step 12：Dockerfile（Lambda Container Image）

```dockerfile
FROM public.ecr.aws/lambda/python:3.11
WORKDIR ${LAMBDA_TASK_ROOT}

COPY pyproject.toml .
RUN pip install uv && uv export --no-dev > requirements.txt && pip install -r requirements.txt

COPY src/agent/ ./agent/
COPY config/ ./config/

# 預設 handler（SAM template 會各自覆寫）
CMD ["agent.webhook_handler.handler"]
```

### Step 13：Makefile（開發常用指令）

```makefile
.PHONY: dev deploy test

dev:
    cp .env.example .env
    uv run uvicorn agent.local_server:app --reload  # 本地模擬 webhook

test:
    uv run pytest tests/ -v

build:
    sam build

deploy:
    sam deploy --guided  # 第一次部署用 --guided，之後直接 sam deploy

set-webhook:
    curl "https://api.telegram.org/bot$(TELEGRAM_BOT_TOKEN)/setWebhook?url=$(WEBHOOK_URL)/webhook"

logs-webhook:
    sam logs -n WebhookFunction --tail

logs-scheduler:
    sam logs -n SchedulerFunction --tail
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

aws ssm put-parameter --name "/earnings-agent/anthropic-api-key" \
  --value "your_key" --type SecureString

aws ssm put-parameter --name "/earnings-agent/telegram-bot-token" \
  --value "your_token" --type SecureString

# 2. Build & Deploy
sam build
sam deploy --guided  # 第一次，之後直接 sam deploy

# 3. 設定 Telegram Webhook（取得 API Gateway URL 後執行）
make set-webhook TELEGRAM_BOT_TOKEN=xxx WEBHOOK_URL=https://xxx.execute-api.ap-northeast-1.amazonaws.com
```

---

## 環境變數取得方式

| 變數 | 取得方式 |
|---|---|
| `FINNHUB_API_KEY` | 註冊 https://finnhub.io |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| `TELEGRAM_BOT_TOKEN` | Telegram 搜尋 @BotFather → `/newbot` |
| `WEBHOOK_URL` | SAM deploy 完成後，從輸出取得 API Gateway URL |

取得 Telegram `chat_id`：
1. 對 Bot 傳任意訊息
2. 呼叫 `https://api.telegram.org/bot{TOKEN}/getUpdates`
3. 從 response 取得 `message.chat.id`

---

## 對話範例

```
用戶：明天有哪些科技股要公布財報？

Agent：[→ get_stocks_by_sector("Technology")]
       [→ get_earnings_calendar(明天, 明天)]

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

- [ ] Telegram 傳訊息，Agent 能理解並以繁體中文回應
- [ ] 詢問產業股票清單，回傳正確結果
- [ ] 詢問特定股票新聞，回傳中文翻譯摘要
- [ ] 詢問分析師預期，回傳清楚格式
- [ ] 每天早上 8:00 主動推播隔天財報提醒
- [ ] `/subscribe` 與 `/unsubscribe` 指令正常運作
- [ ] 多輪對話正常（對話歷史從 DynamoDB 讀取，重啟不遺失）
- [ ] DynamoDB TTL 正常運作（30 天後自動清除舊對話）
- [ ] SAM deploy 成功，Lambda + API Gateway + EventBridge 全部正常

---

## Phase 2 預留擴充點（留 TODO 註解，不實作）

- [ ] 用戶自訂 watchlist 指令（`/watch AAPL`、`/unwatch AAPL`、`/mylist`）
- [ ] Webhook handler 改為非同步：Lambda 先回 200，再用 SQS 處理 Agent（避免 Telegram timeout）
- [ ] 加入股價即時查詢 tool（`get_quote`）
- [ ] 支援財報電話會議逐字稿摘要（Finnhub earnings transcript endpoint）
- [ ] 加入技術指標 tool（RSI、MA、布林通道）
- [ ] CloudWatch Dashboard 監控 Lambda 錯誤率與 Claude API 費用
