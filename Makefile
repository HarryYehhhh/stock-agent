.PHONY: install test lint dev chat check-bot notify-earnings sync-earnings daily-summary sync-dynamodb sync-from-dynamodb build deploy smoke-test-processor ecr-lifecycle set-webhook set-webhook-local delete-webhook logs-webhook logs-processor logs-scheduler ssm-put ddb-tables ddb-scan ddb-count

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

chat:
	cp -n .env.example .env || true
	PYTHONPATH=src uv run --env-file .env python -m agent.chat_cli

# 一鍵健檢 local-first 模式下 bot 是否健康：本地 server / ngrok tunnel /
# Telegram webhook / 金鑰 / Groq API。唯讀，不寫 DB 也不發訊息。
check-bot:
	./scripts/check-bot.sh

notify-earnings:
	PYTHONPATH=src uv run --env-file .env python -m agent.scheduler_handler

sync-earnings:
	PYTHONPATH=src uv run --env-file .env python -m agent.scheduler_handler sync

daily-summary:
	PYTHONPATH=src uv run --env-file .env python -m agent.market_summary

sync-dynamodb:
	PYTHONPATH=src uv run --env-file .env python -m agent.db.upload_local_store --write

sync-from-dynamodb:
	PYTHONPATH=src uv run --env-file .env python -m agent.db.download_remote_store --write

build:
	cd infra && sam build

deploy:
	./scripts/deploy-guard.sh
	cd infra && sam deploy --stack-name $(STACK) --region $(REGION) --capabilities CAPABILITY_IAM --resolve-image-repos --resolve-s3 --confirm-changeset $(if $(GUIDED),--guided,)
	$(MAKE) smoke-test-processor

# Post-deploy smoke test：sam deploy 成功後 invoke 一次 processor Lambda（無副作用的空
# Records payload，見 tests/test_processor_handler.py::test_handler_empty_records_is_noop），
# 確認 cold start 沒有 import 階段的 crash（例如 2026-07-24 groq 套件短暫缺漏那次事故，
# 靠使用者流量觸發 3 次 SQS 重試進 DLQ 才被發現）。fail-closed：aws invoke 這個 CLI 呼叫
# 本身失敗（函式不存在/權限不足/節流/網路錯誤）或 invoke response 帶 FunctionError，
# 都視為失敗，印出錯誤內容並以非零 exit code 結束；只有「invoke 成功且無 FunctionError」
# 才算通過。
smoke-test-processor:
	@echo "==> smoke testing $(STACK)-processor ($(REGION))"
	@RESPONSE_FILE=$$(mktemp); \
	if ! INVOKE_META=$$(aws lambda invoke --function-name $(STACK)-processor --region $(REGION) \
	  --cli-binary-format raw-in-base64-out --payload '{"Records": []}' "$$RESPONSE_FILE" 2>&1); then \
	  echo "❌ processor smoke test FAILED: aws lambda invoke command itself failed"; \
	  echo "$$INVOKE_META"; \
	  rm -f "$$RESPONSE_FILE"; \
	  exit 1; \
	fi; \
	FUNCTION_ERROR=$$(echo "$$INVOKE_META" | jq -r '.FunctionError // empty'); \
	if [ -n "$$FUNCTION_ERROR" ]; then \
	  echo "❌ processor smoke test FAILED: FunctionError=$$FUNCTION_ERROR"; \
	  cat "$$RESPONSE_FILE"; \
	  rm -f "$$RESPONSE_FILE"; \
	  exit 1; \
	fi; \
	rm -f "$$RESPONSE_FILE"; \
	echo "✅ processor smoke test passed (no ImportModuleError)"

# 為 SAM companion stack 自動建立的 ECR repo 掛上 lifecycle policy（只保留最近 3 個 image）。
# repo 由 resolve_image_repos 建立，不在 template.yaml 內，重建 stack 後 repo hash 會變，需重跑本 target。
ecr-lifecycle:
	@for repo in $$(aws ecr describe-repositories --region $(REGION) --output json \
	  | jq -r '.repositories[].repositoryName | select(startswith("earningsagente"))'); do \
	  echo "applying lifecycle policy to $$repo"; \
	  aws ecr put-lifecycle-policy --region $(REGION) --repository-name "$$repo" \
	    --lifecycle-policy-text file://infra/ecr-lifecycle-policy.json; \
	done

ssm-put:
	@test -n "$(FINNHUB_API_KEY)" || (echo "FINNHUB_API_KEY missing" && exit 1)
	@test -n "$(GROQ_API_KEY)" || (echo "GROQ_API_KEY missing" && exit 1)
	@test -n "$(TELEGRAM_BOT_TOKEN)" || (echo "TELEGRAM_BOT_TOKEN missing" && exit 1)
	@test -n "$(TELEGRAM_WEBHOOK_SECRET)" || (echo "TELEGRAM_WEBHOOK_SECRET missing" && exit 1)
	@aws ssm put-parameter --region $(REGION) --overwrite --type SecureString --name /earnings-agent/finnhub-api-key --value "$(FINNHUB_API_KEY)"
	@aws ssm put-parameter --region $(REGION) --overwrite --type SecureString --name /earnings-agent/groq-api-key --value "$(GROQ_API_KEY)"
	@aws ssm put-parameter --region $(REGION) --overwrite --type SecureString --name /earnings-agent/telegram-bot-token --value "$(TELEGRAM_BOT_TOKEN)"
	@aws ssm put-parameter --region $(REGION) --overwrite --type SecureString --name /earnings-agent/telegram-webhook-secret --value "$(TELEGRAM_WEBHOOK_SECRET)"

set-webhook:
	@test -n "$(TELEGRAM_BOT_TOKEN)" || (echo "TELEGRAM_BOT_TOKEN missing" && exit 1)
	@test -n "$(WEBHOOK_URL)" || (echo "WEBHOOK_URL missing" && exit 1)
	@test -n "$(TELEGRAM_WEBHOOK_SECRET)" || (echo "TELEGRAM_WEBHOOK_SECRET missing" && exit 1)
	@curl -sS "https://api.telegram.org/bot$(TELEGRAM_BOT_TOKEN)/setWebhook" \
	  -d "url=$(WEBHOOK_URL)" \
	  -d "secret_token=$(TELEGRAM_WEBHOOK_SECRET)"

set-webhook-local:
	@test -n "$(TELEGRAM_BOT_TOKEN)" || (echo "TELEGRAM_BOT_TOKEN missing" && exit 1)
	@test -n "$(WEBHOOK_URL)" || (echo "WEBHOOK_URL missing" && exit 1)
	@test -n "$(TELEGRAM_WEBHOOK_SECRET)" || (echo "TELEGRAM_WEBHOOK_SECRET missing" && exit 1)
	@curl -sS "https://api.telegram.org/bot$(TELEGRAM_BOT_TOKEN)/setWebhook" \
	  -d "url=$(WEBHOOK_URL)/webhook" \
	  -d "secret_token=$(TELEGRAM_WEBHOOK_SECRET)"

delete-webhook:
	@test -n "$(TELEGRAM_BOT_TOKEN)" || (echo "TELEGRAM_BOT_TOKEN missing" && exit 1)
	@curl -sS "https://api.telegram.org/bot$(TELEGRAM_BOT_TOKEN)/deleteWebhook"

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
