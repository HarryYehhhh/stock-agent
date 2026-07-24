.PHONY: install test lint dev notify-earnings sync-earnings daily-summary sync-dynamodb build deploy set-webhook set-webhook-local delete-webhook logs-webhook logs-processor logs-scheduler ssm-put ddb-tables ddb-scan ddb-count

REGION ?= ap-northeast-1
STACK ?= earnings-agent
DDB_PREFIX ?= earnings-agent

# jq filter: 把 DynamoDB typed JSON（{"S":"x"}）攤平成一般值
DDB_UNWRAP := def u: if type=="object" then (if has("S") then .S elif has("N") then (.N|tonumber) elif has("BOOL") then .BOOL elif has("NULL") then null elif has("M") then (.M|map_values(u)) elif has("L") then (.L|map(u)) elif has("SS") then .SS elif has("NS") then (.NS|map(tonumber)) else map_values(u) end) else . end; (.Items // []) | map(map_values(u))

install:
	uv sync

test:
	PYTHONPATH=src uv run pytest tests/ -v

dev:
	cp -n .env.example .env || true
	mkdir -p logs
	@echo "=== dev start $$(date '+%Y-%m-%d %H:%M:%S') ===" >> logs/dev.log
	PYTHONUNBUFFERED=1 uv run uvicorn agent.local_server:app --reload --port 8080 --env-file .env 2>&1 | tee -a logs/dev.log

notify-earnings:
	PYTHONPATH=src uv run python -m agent.scheduler_handler

sync-earnings:
	PYTHONPATH=src uv run python -m agent.scheduler_handler sync

daily-summary:
	PYTHONPATH=src uv run python -m agent.market_summary

sync-dynamodb:
	PYTHONPATH=src uv run python -m agent.db.upload_local_store --write

build:
	cd infra && sam build

deploy:
	cd infra && sam deploy --stack-name $(STACK) --region $(REGION) --capabilities CAPABILITY_IAM --resolve-image-repos --resolve-s3 $(if $(GUIDED),--guided,)

ssm-put:
	@test -n "$(FINNHUB_API_KEY)" || (echo "FINNHUB_API_KEY missing" && exit 1)
	@test -n "$(GROQ_API_KEY)" || (echo "GROQ_API_KEY missing" && exit 1)
	@test -n "$(TELEGRAM_BOT_TOKEN)" || (echo "TELEGRAM_BOT_TOKEN missing" && exit 1)
	@test -n "$(TELEGRAM_WEBHOOK_SECRET)" || (echo "TELEGRAM_WEBHOOK_SECRET missing" && exit 1)
	aws ssm put-parameter --region $(REGION) --overwrite --type SecureString --name /earnings-agent/finnhub-api-key --value "$(FINNHUB_API_KEY)"
	aws ssm put-parameter --region $(REGION) --overwrite --type SecureString --name /earnings-agent/groq-api-key --value "$(GROQ_API_KEY)"
	aws ssm put-parameter --region $(REGION) --overwrite --type SecureString --name /earnings-agent/telegram-bot-token --value "$(TELEGRAM_BOT_TOKEN)"
	aws ssm put-parameter --region $(REGION) --overwrite --type SecureString --name /earnings-agent/telegram-webhook-secret --value "$(TELEGRAM_WEBHOOK_SECRET)"

set-webhook:
	@test -n "$(TELEGRAM_BOT_TOKEN)" || (echo "TELEGRAM_BOT_TOKEN missing" && exit 1)
	@test -n "$(WEBHOOK_URL)" || (echo "WEBHOOK_URL missing" && exit 1)
	@test -n "$(TELEGRAM_WEBHOOK_SECRET)" || (echo "TELEGRAM_WEBHOOK_SECRET missing" && exit 1)
	curl -sS "https://api.telegram.org/bot$(TELEGRAM_BOT_TOKEN)/setWebhook" \
	  -d "url=$(WEBHOOK_URL)" \
	  -d "secret_token=$(TELEGRAM_WEBHOOK_SECRET)"

set-webhook-local:
	@test -n "$(TELEGRAM_BOT_TOKEN)" || (echo "TELEGRAM_BOT_TOKEN missing" && exit 1)
	@test -n "$(WEBHOOK_URL)" || (echo "WEBHOOK_URL missing" && exit 1)
	@test -n "$(TELEGRAM_WEBHOOK_SECRET)" || (echo "TELEGRAM_WEBHOOK_SECRET missing" && exit 1)
	curl -sS "https://api.telegram.org/bot$(TELEGRAM_BOT_TOKEN)/setWebhook" \
	  -d "url=$(WEBHOOK_URL)/webhook" \
	  -d "secret_token=$(TELEGRAM_WEBHOOK_SECRET)"

delete-webhook:
	@test -n "$(TELEGRAM_BOT_TOKEN)" || (echo "TELEGRAM_BOT_TOKEN missing" && exit 1)
	curl -sS "https://api.telegram.org/bot$(TELEGRAM_BOT_TOKEN)/deleteWebhook"

logs-webhook:
	cd infra && sam logs --stack-name $(STACK) --region $(REGION) -n WebhookFunction --tail

logs-processor:
	cd infra && sam logs --stack-name $(STACK) --region $(REGION) -n ProcessorFunction --tail

logs-scheduler:
	cd infra && sam logs --stack-name $(STACK) --region $(REGION) -n SchedulerFunction --tail

# 列出本 stack 的所有 DynamoDB 表（去掉 prefix）
ddb-tables:
	@aws dynamodb list-tables --region $(REGION) --output json \
	  | jq -r '.TableNames[] | select(startswith("$(DDB_PREFIX)-")) | sub("^$(DDB_PREFIX)-";"")'

# 掃描單一表，輸出攤平後的 JSON。用法：make ddb-scan TABLE=subscribers
# 注意：scan 單次上限 1MB，大表僅回傳第一頁
ddb-scan:
	@test -n "$(TABLE)" || (echo "usage: make ddb-scan TABLE=subscribers" && exit 1)
	@aws dynamodb scan --table-name $(DDB_PREFIX)-$(TABLE) --region $(REGION) --output json \
	  | jq '$(DDB_UNWRAP)'

# 計算表內筆數。用法：make ddb-count TABLE=subscribers
ddb-count:
	@test -n "$(TABLE)" || (echo "usage: make ddb-count TABLE=subscribers" && exit 1)
	@aws dynamodb scan --table-name $(DDB_PREFIX)-$(TABLE) --region $(REGION) --select COUNT --output json \
	  | jq '.Count'
