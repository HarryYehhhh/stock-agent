"""Subscriber list for daily earnings push notifications."""

from __future__ import annotations

from agent.db.backend import get_backend


def subscribe(chat_id: str) -> None:
    get_backend().subscribe(chat_id)


def unsubscribe(chat_id: str) -> None:
    get_backend().unsubscribe(chat_id)


def get_all_active() -> list[str]:
    return get_backend().get_all_active()
