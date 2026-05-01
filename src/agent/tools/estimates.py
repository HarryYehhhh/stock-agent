"""Analyst estimates tool: combines recommendation trends + EPS estimates."""

from __future__ import annotations

import logging

from agent.config import finnhub_api_key
from agent.tools._http import client

log = logging.getLogger(__name__)

ESTIMATES_TOOL = {
    "name": "get_analyst_estimates",
    "description": (
        "Fetch analyst consensus for a US stock symbol: latest buy/hold/sell distribution "
        "and forward EPS estimates. Use when the user asks about analyst expectations."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Stock ticker symbol, e.g. AAPL"},
        },
        "required": ["symbol"],
    },
}


def get_analyst_estimates(symbol: str) -> dict:
    token = finnhub_api_key()

    rec_resp = client().get("/stock/recommendation", params={"symbol": symbol, "token": token})
    rec_resp.raise_for_status()
    recommendations = rec_resp.json()

    # eps-estimate requires a paid Finnhub plan; gracefully skip if 403
    eps_data: list[dict] = []
    try:
        eps_resp = client().get("/stock/eps-estimate", params={"symbol": symbol, "token": token})
        eps_resp.raise_for_status()
        eps_data = eps_resp.json().get("data", [])
    except Exception:
        log.warning("eps-estimate unavailable for %s (likely free-tier limitation)", symbol)

    latest_rec = recommendations[0] if recommendations else {}
    result: dict = {
        "symbol": symbol,
        "recommendation": {
            "period": latest_rec.get("period"),
            "buy": latest_rec.get("buy"),
            "hold": latest_rec.get("hold"),
            "sell": latest_rec.get("sell"),
            "strong_buy": latest_rec.get("strongBuy"),
            "strong_sell": latest_rec.get("strongSell"),
        },
    }

    if eps_data:
        result["eps_estimates"] = [
            {
                "period": d.get("period"),
                "eps_avg": d.get("epsAvg"),
                "eps_high": d.get("epsHigh"),
                "eps_low": d.get("epsLow"),
                "analyst_count": d.get("numberAnalysts"),
            }
            for d in eps_data[:4]
        ]
    else:
        result["eps_estimates_note"] = "EPS estimates unavailable (free-tier API limitation)"

    return result
