"""Earnings calendar tool backed by Finnhub /calendar/earnings."""

from __future__ import annotations

from agent.config import finnhub_api_key
from agent.tools._http import client

EARNINGS_TOOL = {
    "name": "get_earnings_calendar",
    "description": (
        "Fetch US stock earnings calendar between two dates. "
        "Returns a list of upcoming earnings releases with EPS / revenue estimates "
        "and the reporting hour code (bmo=before market open, amc=after market close, dmh=during market hours)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "date_from": {"type": "string", "description": "Start date in YYYY-MM-DD"},
            "date_to": {"type": "string", "description": "End date in YYYY-MM-DD (inclusive)"},
        },
        "required": ["date_from", "date_to"],
    },
}


def get_earnings_calendar(date_from: str, date_to: str) -> list[dict]:
    r = client().get(
        "/calendar/earnings",
        params={"from": date_from, "to": date_to, "token": finnhub_api_key()},
    )
    r.raise_for_status()
    return r.json().get("earningsCalendar", [])[:100]
