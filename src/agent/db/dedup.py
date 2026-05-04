"""Telegram update_id dedup table.

Webhook handler calls claim_update_id() before enqueuing to SQS.
Returns False when the update_id was already claimed, so the handler can
short-circuit and avoid double-billing LLM calls when Telegram retries.
"""

from __future__ import annotations

import time
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from agent.config import aws_region, processed_updates_table

DEDUP_TTL_SECONDS = 3600


@lru_cache(maxsize=1)
def _table():
    return boto3.resource("dynamodb", region_name=aws_region()).Table(processed_updates_table())


def claim_update_id(update_id: int) -> bool:
    """Atomically claim an update_id. Returns True if first time, False if duplicate."""
    try:
        _table().put_item(
            Item={"update_id": update_id, "ttl": int(time.time()) + DEDUP_TTL_SECONDS},
            ConditionExpression="attribute_not_exists(update_id)",
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
