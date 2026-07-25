"""Lambda entry point for Telegram webhook.

Stays under 1 second by offloading agent work to SQS:
  1. validate the X-Telegram-Bot-Api-Secret-Token header
  2. dedup by update_id (DynamoDB conditional put)
  3. rate-limit by chat_id (3/min, 15/day; over-limit messages are dropped)
  4. enqueue the raw update body to SQS
  5. return 200
"""

from __future__ import annotations

import json
from functools import lru_cache

import boto3
from aws_lambda_powertools import Logger

from agent.config import aws_region, telegram_webhook_secret, updates_queue_url
from agent.db.dedup import claim_update_id
from agent.db.rate_limit import check_rate_limit
from agent.telegram_client import send_plain_message

logger = Logger()


@lru_cache(maxsize=1)
def _sqs():
    return boto3.client("sqs", region_name=aws_region())


def _header(event: dict, name: str) -> str | None:
    headers = event.get("headers") or {}
    # API Gateway HTTP API lowercases header names; be defensive anyway.
    return headers.get(name) or headers.get(name.lower())


def _chat_id(body: dict) -> str | None:
    message = body.get("message") or body.get("edited_message")
    if not message:
        return None
    chat = message.get("chat") or {}
    if "id" not in chat:
        return None
    return str(chat["id"])


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

    chat_id = _chat_id(body)
    if chat_id is not None:
        try:
            allowed, notify_text = check_rate_limit(chat_id)
        except Exception:
            # Rate limiting is a protective guard, not core functionality —
            # fail open so an outage in the rate-limit table doesn't take
            # down the whole webhook.
            logger.exception("rate limit check failed for chat %s; failing open", chat_id)
            allowed, notify_text = True, None
        if not allowed:
            logger.info("rate limit reached for chat %s", chat_id)
            if notify_text is not None:
                try:
                    send_plain_message(chat_id, notify_text)
                except Exception:
                    logger.exception("failed to send rate limit notice to chat %s", chat_id)
            return {"statusCode": 200, "body": ""}

    _sqs().send_message(QueueUrl=updates_queue_url(), MessageBody=raw_body)
    return {"statusCode": 200, "body": ""}
