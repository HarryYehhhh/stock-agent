"""Earnings calendar tool backed by Finnhub /calendar/earnings."""

from __future__ import annotations

from agent.config import finnhub_api_key
from agent.tools._http import client

EARNINGS_TOOL = {
    "name": "get_earnings_calendar",
    "description": (
        "Fetch US stock earnings calendar between two dates. "
        "Pass `symbol` to query a single ticker (recommended for ticker-specific queries). "
        "Returns earnings releases with EPS / revenue estimates and reporting hour code "
        "(bmo=before market open, amc=after market close, dmh=during market hours)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "date_from": {"type": "string", "description": "Start date in YYYY-MM-DD"},
            "date_to": {"type": "string", "description": "End date in YYYY-MM-DD (inclusive)"},
            "symbol": {"type": "string", "description": "Optional ticker filter, e.g. AAPL"},
        },
        "required": ["date_from", "date_to"],
    },
}


def get_earnings_calendar(
    date_from: str, date_to: str, symbol: str | None = None
) -> list[dict]:
    params: dict = {"from": date_from, "to": date_to, "token": finnhub_api_key()}
    if symbol:
        params["symbol"] = symbol.upper()
    r = client().get("/calendar/earnings", params=params)
    r.raise_for_status()
    items = r.json().get("earningsCalendar", [])
    # Defensive client-side filter in case Finnhub ignores the symbol param on free tier
    if symbol:
        items = [it for it in items if (it.get("symbol") or "").upper() == symbol.upper()]
    return items[:100]


def get_company_industry(symbol: str) -> str:
    r = client().get(
        "/stock/profile2",
        params={"symbol": symbol, "token": finnhub_api_key()},
    )
    r.raise_for_status()
    return r.json().get("finnhubIndustry", "")
