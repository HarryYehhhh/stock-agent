"""Common fixtures: stub env vars, then start moto for AWS resources.

Tests must not hit real AWS / Groq / Finnhub.
"""

from __future__ import annotations

import os

import boto3
import pytest
from moto import mock_aws

from agent.db import backend

REGION = "ap-northeast-1"

CONVERSATIONS = "test-conversations"
SUBSCRIBERS = "test-subscribers"
PROCESSED = "test-processed"
WATCHLIST = "test-watchlist"
EARNINGS_CALENDAR = "test-earnings-calendar"
EARNINGS_NOTIFICATIONS = "test-earnings-notifications"
COMPANY_SUMMARIES = "test-company-summaries"
NEWS = "test-news"
RATE_LIMITS = "test-rate-limits"
TRACES = "test-traces"
CORRECTIONS = "test-corrections"
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
    monkeypatch.setenv("WATCHLIST_TABLE", WATCHLIST)
    monkeypatch.setenv("EARNINGS_CALENDAR_TABLE", EARNINGS_CALENDAR)
    monkeypatch.setenv("EARNINGS_NOTIFICATIONS_TABLE", EARNINGS_NOTIFICATIONS)
    monkeypatch.setenv("COMPANY_SUMMARIES_TABLE", COMPANY_SUMMARIES)
    monkeypatch.setenv("NEWS_TABLE", NEWS)
    monkeypatch.setenv("RATE_LIMITS_TABLE", RATE_LIMITS)
    monkeypatch.setenv("TRACES_TABLE", TRACES)
    monkeypatch.setenv("CORRECTIONS_TABLE", CORRECTIONS)
    monkeypatch.setenv("FINNHUB_API_KEY", "fake-finnhub")
    monkeypatch.setenv("GROQ_API_KEY", "fake-groq")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "fake-secret")
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("LOCAL_STORE_PATH", raising=False)
    backend.get_backend.cache_clear()


def clear_db_caches() -> None:
    from agent import config as cfg, webhook_handler

    cfg._load_secrets.cache_clear()
    backend.get_backend.cache_clear()
    webhook_handler._sqs.cache_clear()


@pytest.fixture
def aws():
    with mock_aws():
        os.environ["STORAGE_BACKEND"] = "dynamodb"
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
        ddb.create_table(
            TableName=WATCHLIST,
            KeySchema=[
                {"AttributeName": "chat_id", "KeyType": "HASH"},
                {"AttributeName": "symbol", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "chat_id", "AttributeType": "S"},
                {"AttributeName": "symbol", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName=EARNINGS_CALENDAR,
            KeySchema=[
                {"AttributeName": "symbol", "KeyType": "HASH"},
                {"AttributeName": "earning_date", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "symbol", "AttributeType": "S"},
                {"AttributeName": "earning_date", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "earning-date-index",
                    "KeySchema": [{"AttributeName": "earning_date", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName=EARNINGS_NOTIFICATIONS,
            KeySchema=[
                {"AttributeName": "chat_id", "KeyType": "HASH"},
                {"AttributeName": "notify_key", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "chat_id", "AttributeType": "S"},
                {"AttributeName": "notify_key", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName=COMPANY_SUMMARIES,
            KeySchema=[
                {"AttributeName": "symbol", "KeyType": "HASH"},
                {"AttributeName": "earning_date", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "symbol", "AttributeType": "S"},
                {"AttributeName": "earning_date", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName=NEWS,
            KeySchema=[{"AttributeName": "headline", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "headline", "AttributeType": "S"},
                {"AttributeName": "bucket", "AttributeType": "S"},
                {"AttributeName": "published_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "published-at-index",
                    "KeySchema": [
                        {"AttributeName": "bucket", "KeyType": "HASH"},
                        {"AttributeName": "published_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName=RATE_LIMITS,
            KeySchema=[
                {"AttributeName": "chat_id", "KeyType": "HASH"},
                {"AttributeName": "window_key", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "chat_id", "AttributeType": "S"},
                {"AttributeName": "window_key", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName=TRACES,
            KeySchema=[
                {"AttributeName": "chat_id", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "chat_id", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "bucket", "AttributeType": "S"},
                {"AttributeName": "ts", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "trace-time-index",
                    "KeySchema": [
                        {"AttributeName": "bucket", "KeyType": "HASH"},
                        {"AttributeName": "ts", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName=CORRECTIONS,
            KeySchema=[{"AttributeName": "correction_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "correction_id", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "status-index",
                    "KeySchema": [
                        {"AttributeName": "status", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        sqs = boto3.client("sqs", region_name=REGION)
        queue_url = sqs.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]
        os.environ["UPDATES_QUEUE_URL"] = queue_url

        # Reset cached boto3/httpx clients in each test so they pick up the moto env.
        clear_db_caches()

        yield {"sqs": sqs, "queue_url": queue_url, "ddb": ddb}

        clear_db_caches()


@pytest.fixture
def local_store(monkeypatch, tmp_path):
    store_path = tmp_path / "store.sqlite3"
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORE_PATH", str(store_path))
    clear_db_caches()
    yield store_path
    clear_db_caches()
