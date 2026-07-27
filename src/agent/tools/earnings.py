"""Earnings calendar tool backed by Finnhub /calendar/earnings."""

from __future__ import annotations

import logging

from agent.config import finnhub_api_key
from agent.db.backend import get_backend
from agent.tools._http import client

log = logging.getLogger(__name__)

# Field whitelist for /calendar/earnings rows, kept minimal to control token
# usage in the LLM tool-result payload. symbol/date/hour/epsEstimate/
# revenueEstimate are also the only fields the DB backends persist
# (see db/backend.py upsert_earnings_calendar); epsActual/revenueActual/
# quarter/year are kept for LLM-facing queries about past earnings but are
# not currently read by any cache/scheduler consumer.
EARNINGS_FIELDS = (
    "symbol",
    "date",
    "hour",
    "epsEstimate",
    "epsActual",
    "revenueEstimate",
    "revenueActual",
    "quarter",
    "year",
)
EARNINGS_ROW_LIMIT = 50

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


def _select_rows(items: list[dict], limit: int) -> list[dict]:
    """Thin `items` down to `limit` rows with date coverage prioritized over
    strict truncation: group by date, rank each date's rows by revenueEstimate
    descending (missing/None last, as a proxy for company size), then
    round-robin across dates (earliest date first) so every date in range
    keeps a representative instead of the whole budget going to whichever
    date happens to sort first alphabetically by symbol."""
    if len(items) <= limit:
        return items

    groups: dict[str | None, list[dict]] = {}
    for it in items:
        groups.setdefault(it.get("date"), []).append(it)
    for rows in groups.values():
        rows.sort(key=lambda it: (it.get("revenueEstimate") is None, -(it.get("revenueEstimate") or 0)))
    dates_in_order = sorted(groups, key=lambda d: (d is None, d))

    selected: list[dict] = []
    round_idx = 0
    while len(selected) < limit:
        added_any = False
        for date in dates_in_order:
            rows = groups[date]
            if round_idx < len(rows):
                selected.append(rows[round_idx])
                added_any = True
                if len(selected) == limit:
                    break
        if not added_any:
            break
        round_idx += 1
    return selected


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
    items = _select_rows(items, EARNINGS_ROW_LIMIT)
    items = [
        {k: it[k] for k in EARNINGS_FIELDS if k in it}
        for it in items
    ]
    try:
        get_backend().upsert_earnings_calendar(items)
    except Exception as e:
        log.warning("earnings calendar cache write failed: %s", e)
    return items


def get_company_industry(symbol: str) -> str:
    r = client().get(
        "/stock/profile2",
        params={"symbol": symbol, "token": finnhub_api_key()},
    )
    r.raise_for_status()
    return r.json().get("finnhubIndustry", "")
