"""Common fixtures: stub env vars so config.py doesn't blow up at import time."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "fake-finnhub")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-bot")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
