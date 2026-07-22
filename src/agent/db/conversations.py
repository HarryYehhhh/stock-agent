"""Conversation history persistence.

History is fetched newest-first then reversed for chronological order before
being passed to the LLM.
"""

from __future__ import annotations

from agent.db.backend import get_backend

HISTORY_LIMIT = 20


def save_message(chat_id: str, role: str, content: str) -> None:
    get_backend().save_message(chat_id, role, content)


def get_history(chat_id: str, limit: int = HISTORY_LIMIT) -> list[dict]:
    """Return up to `limit` recent messages, oldest first."""
    return get_backend().get_history(chat_id, limit)


def build_messages(chat_id: str) -> list[dict]:
    """Format DynamoDB rows into message dicts (role + text content)."""
    return [{"role": item["role"], "content": item["content"]} for item in get_history(chat_id)]
