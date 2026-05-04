.PHONY: install test build deploy logs-scheduler invoke-scheduler

REGION ?= ap-northeast-1
STACK ?= earnings-agent

install:
	uv sync

test:
	PYTHONPATH=src uv run pytest tests/ -v

build:
	cd infra && sam build

deploy:
	@test -n "$(FINNHUB_API_KEY)" || (echo "FINNHUB_API_KEY missing" && exit 1)
	@test -n "$(GEMINI_API_KEY)" || (echo "GEMINI_API_KEY missing" && exit 1)
	@test -n "$(TELEGRAM_BOT_TOKEN)" || (echo "TELEGRAM_BOT_TOKEN missing" && exit 1)
	@test -n "$(TELEGRAM_CHAT_ID)" || (echo "TELEGRAM_CHAT_ID missing" && exit 1)
	cd infra && sam deploy \
	  --stack-name $(STACK) --region $(REGION) \
	  --capabilities CAPABILITY_IAM --resolve-image-repos --resolve-s3 \
	  --parameter-overrides \
	    FinnhubApiKey=$(FINNHUB_API_KEY) \
	    GeminiApiKey=$(GEMINI_API_KEY) \
	    TelegramBotToken=$(TELEGRAM_BOT_TOKEN) \
	    TelegramChatId=$(TELEGRAM_CHAT_ID) \
	  $(if $(GUIDED),--guided,)

logs-scheduler:
	cd infra && sam logs --stack-name $(STACK) --region $(REGION) -n SchedulerFunction --tail

invoke-scheduler:
	aws lambda invoke --region $(REGION) --function-name $(STACK)-scheduler /tmp/scheduler-out.json && cat /tmp/scheduler-out.json
