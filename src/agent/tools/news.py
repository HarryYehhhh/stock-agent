"""Company news tool backed by Finnhub /company-news.

Returns raw English text (headlines are filtered for marketing/listicle junk
first); the LLM is responsible for translating the remaining items to
Traditional Chinese in the final response.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from agent.config import finnhub_api_key
from agent.tools._http import client

# Marketing/listicle headlines carry no substantive company news (e.g. "join
# us for our webinar", "5 stocks to watch this week").
# Conservative by design: prefer false negatives (junk slipping through) over
# false positives (real news getting dropped). Two patterns were deliberately
# left out after review because they clipped legitimate reporting:
# - a standalone "most active(ly) ... stocks/tickers" match (e.g. "AMC among
#   most actively traded stocks as retail interest surges" is real news, not
#   a listicle); the plain "join us"/"tune in" alternatives below still catch
#   the actual promo listicles ("Most active stocks — join us as we dive in").
# - a standalone "top gainer(s)/loser(s)" match (e.g. "Tesla was the top
#   gainer in the S&P 500 today after earnings beat" is a real per-stock
#   headline); only the paired listicle phrasing "gainers and/& losers" is
#   kept.
# `\b` word boundaries prevent substring false positives, e.g. "subscribe"
# must not match "subscriber"/"subscribers".
JUNK_HEADLINE_PATTERN = re.compile(
    r"\bjoin\s+us\b|\btune\s+in\b|\bsign\s+up\b|\bregister\s+now\b"
    r"|\bwebinar\b|\bsubscribe\b"
    r"|\bstocks\s+to\s+watch\b|\btrending\s+tickers?\b"
    r"|\bgainers?\s+(?:and|&)\s+losers?\b",
    re.IGNORECASE,
)


def is_junk_headline(headline: str | None) -> bool:
    """True if `headline` looks like marketing/listicle filler rather than
    substantive news."""
    return bool(headline) and bool(JUNK_HEADLINE_PATTERN.search(headline))


# Finnhub's /company-news is mention-based tagging: an article gets tagged
# to a symbol if it mentions that company anywhere in the body, even just in
# passing (e.g. an Intel-earnings story mentioning Apple as a customer gets
# tagged AAPL). `is_headline_relevant()` narrows Big 7 ingestion to articles
# whose *headline* actually names the company, not just its ticker showing
# up in Finnhub's tagging.
BIG7_ALIASES: dict[str, list[str]] = {
    "AAPL": ["Apple"],
    "MSFT": ["Microsoft"],
    "GOOGL": ["Google", "Alphabet"],
    "AMZN": ["Amazon"],
    "NVDA": ["Nvidia"],
    "META": ["Meta", "Facebook"],
    "TSLA": ["Tesla"],
}

# `\b` word boundaries prevent substring false positives, e.g. "Meta" must
# not match "Metaverse".
_BIG7_HEADLINE_PATTERNS = {
    symbol: re.compile(
        r"\b(?:" + "|".join(re.escape(name) for name in [symbol, *aliases]) + r")\b",
        re.IGNORECASE,
    )
    for symbol, aliases in BIG7_ALIASES.items()
}


def is_headline_relevant(symbol: str, headline: str | None) -> bool:
    """True if `headline` names the Big 7 `symbol` itself — its ticker or a
    known company alias (case-insensitive, word-boundary matched). Symbols
    outside `BIG7_ALIASES` fall back to matching the ticker alone."""
    if not headline:
        return False
    pattern = _BIG7_HEADLINE_PATTERNS.get(symbol)
    if pattern is None:
        pattern = re.compile(rf"\b{re.escape(symbol)}\b", re.IGNORECASE)
    return bool(pattern.search(headline))


# High-credibility wire services / financial media, used to flag stored
# news for later consumers to filter/prioritize (ingestion itself is not
# filtered). Comparison is case-insensitive exact match against the whole
# `source` string — no substring matching — so e.g. "Bloomberg Law" or "via
# Bloomberg" do NOT count as Bloomberg.
HIGH_CREDIBILITY_SOURCES: frozenset[str] = frozenset(
    name.casefold()
    for name in (
        "Reuters",
        "Dow Jones",
        "Associated Press",
        "AP",
        "CNBC",
        "Financial Times",
        "The Wall Street Journal",
        "Wall Street Journal",
        "Bloomberg",
    )
)


def is_high_credibility_source(source: str | None) -> bool:
    """True if `source` is an exact (case-insensitive) match for a
    whitelisted high-credibility outlet. Missing/empty source is False."""
    if not source:
        return False
    return source.casefold() in HIGH_CREDIBILITY_SOURCES


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
    items = [it for it in r.json() if not is_junk_headline(it.get("headline"))]
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
