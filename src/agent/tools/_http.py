"""Shared httpx client for Finnhub calls. Sync mode keeps Lambda code simple."""

from __future__ import annotations

import httpx

FINNHUB_BASE = "https://finnhub.io/api/v1"
_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=FINNHUB_BASE, timeout=10.0)
    return _client
