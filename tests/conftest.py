"""Common fixtures: stub env vars, then start moto for AWS resources.

Tests must not hit real AWS / Gemini / Finnhub.
"""

from __future__ import annotations

import os

import boto3
import pytest
from moto import mock_aws

REGION = "ap-northeast-1"

CONVERSATIONS = "test-conversations"
SUBSCRIBERS = "test-subscribers"
PROCESSED = "test-processed"
QUEUE_NAME = "test-updates"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("CONVERSATIONS_TABLE", CONVERSATIONS)
    monkeypatch.setenv("SUBSCRIBERS_TABLE", SUBSCRIBERS)
    monkeypatch.setenv("PROCESSED_UPDATES_TABLE", PROCESSED)
    monkeypatch.setenv("FINNHUB_API_KEY", "fake-finnhub")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "fake-secret")
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)


@pytest.fixture
def aws():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=REGION)
        ddb.create_table(
            TableName=CONVERSATIONS,
            KeySchema=[
                {"AttributeName": "chat_id", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "chat_id", "AttributeType": "S"},
                {"AttributeName": "timestamp", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName=SUBSCRIBERS,
            KeySchema=[{"AttributeName": "chat_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "chat_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName=PROCESSED,
            KeySchema=[{"AttributeName": "update_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "update_id", "AttributeType": "N"}],
            BillingMode="PAY_PER_REQUEST",
        )
        sqs = boto3.client("sqs", region_name=REGION)
        queue_url = sqs.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]
        os.environ["UPDATES_QUEUE_URL"] = queue_url

        # Reset cached boto3/httpx clients in each test so they pick up the moto env.
        from agent import config as cfg, webhook_handler
        from agent.db import conversations, dedup, subscribers

        cfg._load_secrets.cache_clear()
        dedup._table.cache_clear()
        conversations._table.cache_clear()
        subscribers._table.cache_clear()
        webhook_handler._sqs.cache_clear()

        yield {"sqs": sqs, "queue_url": queue_url, "ddb": ddb}
