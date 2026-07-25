"""Daily market summary tool wrapping agent.market_summary.generate_market_summary.

Generates the summary on demand from stored news + live index quotes (both
backends have news; DynamoDB's insert_market_summary is a no-op, so there is
nothing persisted to read back — see ADR-0006).

Note: agent.market_summary imports agent.agent, which imports agent.tools
(this package) — importing generate_market_summary at module level here
would create a circular import. The import is deferred to call time instead.
"""

from __future__ import annotations

MARKET_SUMMARY_TOOL = {
    "name": "get_market_summary",
    "description": (
        "Generate today's US stock market summary: Big 7 tech stock news, AI "
        "industry news and major index performance, digested into a Traditional "
        "Chinese briefing. Use this for questions about the overall market or "
        "daily market recap — do not guess an index ticker symbol and call "
        "get_quote instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "days_back": {
                "type": "integer",
                "description": (
                    "How many days of news to cover (1-7, default 1). Map the "
                    "user's time phrase: today/今天/本日 -> 1, this "
                    "week/本週/近一週/過去七天 -> 7. Default to 1 when no time "
                    "phrase is given."
                ),
                "minimum": 1,
                "maximum": 7,
            },
        },
        "required": [],
    },
}


def get_market_summary(days_back: int = 1) -> dict:
    from agent.market_summary import generate_market_summary

    result = generate_market_summary(days_back)
    return {"summary": result["summary"], "date_range": result["date_range"]}
