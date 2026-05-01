"""Conversation history persistence in DynamoDB.

PK = chat_id (S), SK = timestamp (S, ISO 8601).
TTL drops items 30 days after write.
History is fetched newest-first then reversed for chronological order before
being passed to Claude.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from functools import lru_cache

import boto3
from boto3.dynamodb.conditions import Key

from agent.config import aws_region, conversations_table

CONVERSATION_TTL_SECONDS = 30 * 24 * 3600
HISTORY_LIMIT = 20


@lru_cache(maxsize=1)
def _table():
    return boto3.resource("dynamodb", region_name=aws_region()).Table(conversations_table())


def save_message(chat_id: str, role: str, content: str) -> None:
    _table().put_item(
        Item={
            "chat_id": chat_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "content": content,
            "ttl": int(time.time()) + CONVERSATION_TTL_SECONDS,
        }
    )


def get_history(chat_id: str, limit: int = HISTORY_LIMIT) -> list[dict]:
    """Return up to `limit` recent messages, oldest first."""
    resp = _table().query(
        KeyConditionExpression=Key("chat_id").eq(chat_id),
        ScanIndexForward=False,
        Limit=limit,
    )
    items = resp.get("Items", [])
    items.reverse()
    return items


def build_messages_for_claude(chat_id: str) -> list[dict]:
    """Format DynamoDB rows into Claude messages API shape (role + text content)."""
    return [{"role": item["role"], "content": item["content"]} for item in get_history(chat_id)]
