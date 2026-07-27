"""Conversation history persistence.

History is fetched newest-first then reversed for chronological order before
being passed to the LLM.
"""

from __future__ import annotations

from agent.db.backend import get_backend

HISTORY_LIMIT = 10
HISTORY_CHAR_BUDGET = 6000


def save_message(chat_id: str, role: str, content: str) -> None:
    get_backend().save_message(chat_id, role, content)


def get_history(chat_id: str, limit: int = HISTORY_LIMIT) -> list[dict]:
    """Return up to `limit` recent messages, oldest first."""
    return get_backend().get_history(chat_id, limit)


def _apply_char_budget(items: list[dict], budget: int = HISTORY_CHAR_BUDGET) -> list[dict]:
    """Drop whole messages from the oldest end until total content length
    fits within `budget`. Never truncates a single message's content; if the
    newest message alone exceeds the budget, it is kept on its own."""
    total = sum(len(item["content"]) for item in items)
    start = 0
    while total > budget and start < len(items) - 1:
        total -= len(items[start]["content"])
        start += 1
    return items[start:]


def build_messages(chat_id: str) -> list[dict]:
    """Format DynamoDB rows into message dicts (role + text content)."""
    history = _apply_char_budget(get_history(chat_id))
    return [{"role": item["role"], "content": item["content"]} for item in history]
