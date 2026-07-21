.PHONY: install test lint dev notify-earnings build deploy set-webhook logs-webhook logs-processor logs-scheduler ssm-put

REGION ?= ap-northeast-1
STACK ?= earnings-agent

install:
	uv sync

test:
	PYTHONPATH=src uv run pytest tests/ -v

dev:
	cp -n .env.example .env || true
	uv run uvicorn agent.local_server:app --reload --port 8080

notify-earnings:
	PYTHONPATH=src uv run python -m agent.scheduler_handler

build:
	cd infra && sam build

deploy:
	cd infra && sam deploy --stack-name $(STACK) --region $(REGION) --capabilities CAPABILITY_IAM --resolve-image-repos --resolve-s3 $(if $(GUIDED),--guided,)

ssm-put:
	@test -n "$(FINNHUB_API_KEY)" || (echo "FINNHUB_API_KEY missing" && exit 1)
	@test -n "$(GEMINI_API_KEY)" || (echo "GEMINI_API_KEY missing" && exit 1)
	@test -n "$(TELEGRAM_BOT_TOKEN)" || (echo "TELEGRAM_BOT_TOKEN missing" && exit 1)
	@test -n "$(TELEGRAM_WEBHOOK_SECRET)" || (echo "TELEGRAM_WEBHOOK_SECRET missing" && exit 1)
	aws ssm put-parameter --region $(REGION) --overwrite --type SecureString --name /earnings-agent/finnhub-api-key --value "$(FINNHUB_API_KEY)"
	aws ssm put-parameter --region $(REGION) --overwrite --type SecureString --name /earnings-agent/gemini-api-key --value "$(GEMINI_API_KEY)"
	aws ssm put-parameter --region $(REGION) --overwrite --type SecureString --name /earnings-agent/telegram-bot-token --value "$(TELEGRAM_BOT_TOKEN)"
	aws ssm put-parameter --region $(REGION) --overwrite --type SecureString --name /earnings-agent/telegram-webhook-secret --value "$(TELEGRAM_WEBHOOK_SECRET)"

set-webhook:
	@test -n "$(TELEGRAM_BOT_TOKEN)" || (echo "TELEGRAM_BOT_TOKEN missing" && exit 1)
	@test -n "$(WEBHOOK_URL)" || (echo "WEBHOOK_URL missing" && exit 1)
	@test -n "$(TELEGRAM_WEBHOOK_SECRET)" || (echo "TELEGRAM_WEBHOOK_SECRET missing" && exit 1)
	curl -sS "https://api.telegram.org/bot$(TELEGRAM_BOT_TOKEN)/setWebhook" \
	  -d "url=$(WEBHOOK_URL)" \
	  -d "secret_token=$(TELEGRAM_WEBHOOK_SECRET)"

logs-webhook:
	cd infra && sam logs --stack-name $(STACK) --region $(REGION) -n WebhookFunction --tail

logs-processor:
	cd infra && sam logs --stack-name $(STACK) --region $(REGION) -n ProcessorFunction --tail

logs-scheduler:
	cd infra && sam logs --stack-name $(STACK) --region $(REGION) -n SchedulerFunction --tail
