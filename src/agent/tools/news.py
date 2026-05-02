"""Company news tool backed by Finnhub /company-news.

Returns raw English text; the LLM is responsible for translating to Traditional
Chinese in the final response.
"""

from __future__ import annotations

from datetime import date, timedelta

from agent.config import finnhub_api_key
from agent.tools._http import client

NEWS_TOOL = {
    "name": "get_stock_news",
    "description": (
        "Fetch the latest news headlines for a US stock symbol. "
        "Returns up to 5 news items with headline, summary, source URL and timestamp."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Stock ticker symbol"},
            "days_back": {
                "type": "integer",
                "description": "How many days back to look (1-30, default 7)",
                "minimum": 1,
                "maximum": 30,
            },
        },
        "required": ["symbol"],
    },
}


def get_stock_news(symbol: str, days_back: int = 7) -> list[dict]:
    days_back = max(1, min(30, days_back))
    today = date.today()
    date_from = (today - timedelta(days=days_back)).isoformat()
    date_to = today.isoformat()

    r = client().get(
        "/company-news",
        params={
            "symbol": symbol,
            "from": date_from,
            "to": date_to,
            "token": finnhub_api_key(),
        },
    )
    r.raise_for_status()
    items = r.json()
    return [
        {
            "headline": it.get("headline"),
            "summary": it.get("summary"),
            "url": it.get("url"),
            "datetime": it.get("datetime"),
            "source": it.get("source"),
        }
        for it in items[:5]
    ]
