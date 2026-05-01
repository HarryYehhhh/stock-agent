"""Lambda entry point for Telegram webhook.

Stays under 1 second by offloading agent work to SQS:
  1. validate the X-Telegram-Bot-Api-Secret-Token header
  2. dedup by update_id (DynamoDB conditional put)
  3. enqueue the raw update body to SQS
  4. return 200
"""

from __future__ import annotations

import json
from functools import lru_cache

import boto3
from aws_lambda_powertools import Logger

from agent.config import aws_region, telegram_webhook_secret, updates_queue_url
from agent.db.dedup import claim_update_id

logger = Logger()


@lru_cache(maxsize=1)
def _sqs():
    return boto3.client("sqs", region_name=aws_region())


def _header(event: dict, name: str) -> str | None:
    headers = event.get("headers") or {}
    # API Gateway HTTP API lowercases header names; be defensive anyway.
    return headers.get(name) or headers.get(name.lower())


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    secret = _header(event, "X-Telegram-Bot-Api-Secret-Token")
    if secret != telegram_webhook_secret():
        logger.warning("rejected webhook with bad secret token")
        return {"statusCode": 401, "body": ""}

    raw_body = event.get("body") or "{}"
    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": ""}

    update_id = body.get("update_id")
    if update_id is None:
        return {"statusCode": 200, "body": ""}

    if not claim_update_id(int(update_id)):
        logger.info("duplicate update_id %s ignored", update_id)
        return {"statusCode": 200, "body": ""}

    _sqs().send_message(QueueUrl=updates_queue_url(), MessageBody=raw_body)
    return {"statusCode": 200, "body": ""}
