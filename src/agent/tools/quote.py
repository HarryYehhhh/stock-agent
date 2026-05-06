"""Real-time quote tool backed by Finnhub /quote (free tier)."""

from __future__ import annotations

from agent.config import finnhub_api_key
from agent.tools._http import client

QUOTE_TOOL = {
    "name": "get_quote",
    "description": (
        "Fetch the latest real-time quote for a US stock symbol: current price, "
        "day change, percent change, day high/low, open and previous close."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Stock ticker symbol, e.g. AAPL"},
        },
        "required": ["symbol"],
    },
}


def get_quote(symbol: str) -> dict:
    r = client().get(
        "/quote",
        params={"symbol": symbol.upper(), "token": finnhub_api_key()},
    )
    r.raise_for_status()
    data = r.json()
    return {
        "symbol": symbol.upper(),
        "current_price": data.get("c"),
        "change": data.get("d"),
        "change_percent": data.get("dp"),
        "day_high": data.get("h"),
        "day_low": data.get("l"),
        "open_price": data.get("o"),
        "prev_close": data.get("pc"),
    }
