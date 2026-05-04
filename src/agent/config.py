"""Centralised config loader. All values come from Lambda env vars."""

from __future__ import annotations

import os


def finnhub_api_key() -> str:
    return os.environ["FINNHUB_API_KEY"]


def gemini_api_key() -> str:
    return os.environ["GEMINI_API_KEY"]


def telegram_bot_token() -> str:
    return os.environ["TELEGRAM_BOT_TOKEN"]


def telegram_chat_id() -> int:
    return int(os.environ["TELEGRAM_CHAT_ID"])
