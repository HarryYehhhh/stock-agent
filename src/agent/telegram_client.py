"""Thin wrapper over the Telegram Bot HTTP API."""

from __future__ import annotations

import logging
from functools import lru_cache

import httpx

from agent.config import telegram_bot_token

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LEN = 4000  # below Telegram 4096 limit, leaves room for chunk markers


@lru_cache(maxsize=1)
def _client() -> httpx.Client:
    return httpx.Client(base_url=f"{TELEGRAM_API}/bot{telegram_bot_token()}", timeout=10.0)


def _chunks(text: str, size: int = MAX_MESSAGE_LEN) -> list[str]:
    if len(text) <= size:
        return [text]
    return [text[i : i + size] for i in range(0, len(text), size)]


def send_message(chat_id: str, text: str, parse_mode: str = "Markdown") -> None:
    for chunk in _chunks(text):
        try:
            r = _client().post(
                "/sendMessage",
                json={"chat_id": chat_id, "text": chunk, "parse_mode": parse_mode},
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Markdown parse errors → retry without parse_mode so user still sees the text
            log.warning("sendMessage failed (%s), retrying without parse_mode", e)
            r = _client().post("/sendMessage", json={"chat_id": chat_id, "text": chunk})
            r.raise_for_status()


def send_plain_message(chat_id: str, text: str, timeout: float = 3.0) -> None:
    """Single-shot, low-latency send: no Markdown, no HTTP-error retry
    fallback, short per-request timeout. For callers on a tight timeout
    budget (e.g. the 10s webhook Lambda) that must not risk send_message()'s
    worst case (~2x the client's 10s timeout across the Markdown retry).
    Raises on failure — the caller is responsible for catching it.
    """
    r = _client().post(
        "/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=timeout,
    )
    r.raise_for_status()


def send_typing_action(chat_id: str) -> None:
    try:
        _client().post("/sendChatAction", json={"chat_id": chat_id, "action": "typing"})
    except Exception:
        log.debug("sendChatAction failed", exc_info=True)
