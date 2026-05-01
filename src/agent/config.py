"""Centralised config loader.

In Lambda, secrets are read from SSM Parameter Store.
Locally, secrets are read from environment variables (load .env beforehand).
Resource names (table names, queue URLs) always come from env vars so the same
image can target different stacks.
"""

from __future__ import annotations

import os
from functools import lru_cache

import boto3

SSM_PREFIX = "/earnings-agent"
SSM_PARAM_NAMES = [
    f"{SSM_PREFIX}/finnhub-api-key",
    f"{SSM_PREFIX}/gemini-api-key",
    f"{SSM_PREFIX}/telegram-bot-token",
    f"{SSM_PREFIX}/telegram-webhook-secret",
]


def _is_lambda() -> bool:
    return bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))


@lru_cache(maxsize=1)
def _load_secrets() -> dict[str, str]:
    if _is_lambda():
        ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))
        resp = ssm.get_parameters(Names=SSM_PARAM_NAMES, WithDecryption=True)
        return {p["Name"].split("/")[-1]: p["Value"] for p in resp["Parameters"]}

    return {
        "finnhub-api-key": os.environ["FINNHUB_API_KEY"],
        "gemini-api-key": os.environ["GEMINI_API_KEY"],
        "telegram-bot-token": os.environ["TELEGRAM_BOT_TOKEN"],
        "telegram-webhook-secret": os.environ["TELEGRAM_WEBHOOK_SECRET"],
    }


def finnhub_api_key() -> str:
    return _load_secrets()["finnhub-api-key"]


def gemini_api_key() -> str:
    return _load_secrets()["gemini-api-key"]


def telegram_bot_token() -> str:
    return _load_secrets()["telegram-bot-token"]


def telegram_webhook_secret() -> str:
    return _load_secrets()["telegram-webhook-secret"]


def conversations_table() -> str:
    return os.environ.get("CONVERSATIONS_TABLE", "earnings-agent-conversations")


def subscribers_table() -> str:
    return os.environ.get("SUBSCRIBERS_TABLE", "earnings-agent-subscribers")


def processed_updates_table() -> str:
    return os.environ.get("PROCESSED_UPDATES_TABLE", "earnings-agent-processed-updates")


def updates_queue_url() -> str:
    return os.environ["UPDATES_QUEUE_URL"]


def aws_region() -> str:
    return os.environ.get("AWS_REGION", "ap-northeast-1")
