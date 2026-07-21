"""Subscriber list for daily earnings push notifications."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

import boto3

from agent.config import aws_region, subscribers_table
from agent.db.backend import get_backend, selected_backend_name


@lru_cache(maxsize=1)
def _table():
    return boto3.resource("dynamodb", region_name=aws_region()).Table(subscribers_table())


def subscribe(chat_id: str) -> None:
    if selected_backend_name() == "local":
        get_backend().subscribe(chat_id)
        return

    _table().put_item(
        Item={
            "chat_id": chat_id,
            "subscribed_at": datetime.now(timezone.utc).isoformat(),
            "active": True,
        }
    )


def unsubscribe(chat_id: str) -> None:
    if selected_backend_name() == "local":
        get_backend().unsubscribe(chat_id)
        return

    _table().update_item(
        Key={"chat_id": chat_id},
        UpdateExpression="SET active = :a",
        ExpressionAttributeValues={":a": False},
    )


def get_all_active() -> list[str]:
    """Scan all subscribers with active=True. Acceptable for small subscriber counts."""
    if selected_backend_name() == "local":
        return get_backend().get_all_active()

    chat_ids: list[str] = []
    resp = _table().scan(
        FilterExpression="active = :a",
        ExpressionAttributeValues={":a": True},
    )
    chat_ids.extend(item["chat_id"] for item in resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = _table().scan(
            FilterExpression="active = :a",
            ExpressionAttributeValues={":a": True},
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        chat_ids.extend(item["chat_id"] for item in resp.get("Items", []))
    return chat_ids
