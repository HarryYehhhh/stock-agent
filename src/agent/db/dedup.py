"""Telegram update_id dedup table.

Webhook handler calls claim_update_id() before enqueuing to SQS.
Returns False when the update_id was already claimed, so the handler can
short-circuit and avoid double-billing LLM calls when Telegram retries.
"""

from __future__ import annotations

from agent.db.backend import get_backend


def claim_update_id(update_id: int) -> bool:
    """Atomically claim an update_id. Returns True if first time, False if duplicate."""
    return get_backend().claim_update_id(update_id)
