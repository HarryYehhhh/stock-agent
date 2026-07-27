"""Fast-path shortcut for high-confidence earnings-date questions.

Skips the Groq LLM entirely for queries shaped like "META 的財報是什麼時候" /
"AAPL 財報日期" by matching a ticker adjacent to the 財報 keyword plus a
date-query keyword, then answering directly from `get_earnings_calendar`.
The ticker must sit immediately next to 財報 (only whitespace/的/a single
date keyword + 發 allowed in between) and must not be a known non-ticker
abbreviation (see `_EXCLUDED_TICKERS`), so generic phrases like "AI 相關企業
的財報公布日期" or "EPS 財報日期怎麼看" don't get misread as a single-stock
query. Deliberately conservative: any ambiguity falls through to `None` so
the caller routes to the normal LLM tool-use loop.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from agent.tools.earnings import get_earnings_calendar

FAST_PATH_WINDOW_DAYS = 90

# Ticker style mirrors processor_handler.SYMBOL_RE (1-10 letters, optional
# . or - + 1-3 letter suffix). Duplicated here rather than imported to avoid
# a circular import (processor_handler -> agent.agent -> fast_path).
_SYMBOL_RE = re.compile(r"[A-Z]{1,10}([.\-][A-Z]{1,3})?")

# Matches a run of ASCII letters (with optional . / - suffix) that is not
# itself adjacent to more ASCII letters/digits, so tickers glued directly to
# Chinese text (e.g. "META的財報") are still extracted — a plain \b boundary
# fails here because \w in Unicode mode treats CJK characters as word chars.
_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,10}(?:[.\-][A-Za-z]{1,3})?)(?![A-Za-z0-9])")

_EARNINGS_KEYWORDS = ("財報",)
_DATE_QUERY_KEYWORDS = ("什麼時候", "何時", "日期", "哪天", "哪一天", "幾號")

# Common non-ticker abbreviations that are otherwise indistinguishable from a
# real ticker (all-caps ASCII, 1-10 chars) and can sit directly next to 財報
# in an ordinary sentence (e.g. "EPS 財報日期怎麼看"), which would still pass
# the adjacency check below. Excluded outright regardless of adjacency.
_EXCLUDED_TICKERS = frozenset(
    {
        "AI", "IT", "ETF", "GDP", "CPI", "SEC", "IPO", "ESG",
        "CNN", "CEO", "CFO", "EPS", "FED", "PMI", "CES",
    }
)

_HOUR_LABELS = {"bmo": "盤前", "amc": "盤後", "dmh": "盤中"}


def extract_ticker(text: str) -> str | None:
    """Return the first token in `text` that matches the ticker style, or
    None. Case-insensitive on input, always returns uppercase."""
    for match in _TOKEN_RE.finditer(text):
        candidate = match.group(1).upper()
        if _SYMBOL_RE.fullmatch(candidate):
            return candidate
    return None


def _is_ticker_adjacent_to_earnings(text: str, ticker: str) -> bool:
    """True if `ticker` sits immediately next to the 財報 keyword in `text`,
    allowing only a small connector: optional whitespace/的, optionally
    followed by a single date-query keyword + 發 (e.g. "NVDA 什麼時候發財報").
    Rejects cases like "AI 相關企業的財報" where the ticker-shaped token is
    just a generic word elsewhere in the sentence, unrelated to 財報."""
    date_kw_alt = "|".join(re.escape(kw) for kw in _DATE_QUERY_KEYWORDS)
    pattern = re.compile(
        rf"{re.escape(ticker)}\s*(?:的)?\s*(?:(?:{date_kw_alt})\s*發\s*)?財報",
        re.IGNORECASE,
    )
    return pattern.search(text) is not None


def _format_hour(hour: str | None) -> str:
    return _HOUR_LABELS.get(hour, "時段未定")


def _format_eps(value) -> str | None:
    if value is None:
        return None
    return f"${value:.2f}"


def _format_revenue(value) -> str | None:
    if value is None:
        return None
    if abs(value) >= 1e9:
        return f"{value / 1e9:.1f}B"
    return f"{value / 1e6:.1f}M"


def _format_row(row: dict) -> str:
    line = f"• {row.get('date', '日期未知')}（{_format_hour(row.get('hour'))}）"
    extras = []
    eps = _format_eps(row.get("epsEstimate"))
    if eps is not None:
        extras.append(f"EPS 預估 {eps}")
    revenue = _format_revenue(row.get("revenueEstimate"))
    if revenue is not None:
        extras.append(f"營收預估 {revenue}")
    if extras:
        line += "，" + "，".join(extras)
    return line


def _format_answer(ticker: str, rows: list[dict]) -> str:
    if not rows:
        return f"未來 {FAST_PATH_WINDOW_DAYS} 天內查無 {ticker} 的財報日期"
    lines = [f"*{ticker}* 未來 {FAST_PATH_WINDOW_DAYS} 天內的財報日期：", *(_format_row(r) for r in rows)]
    return "\n".join(lines)


def try_fast_answer(text: str) -> str | None:
    """Return a ready-to-send answer for a high-confidence "ticker 財報 日期"
    query, or None if the query doesn't match or the lookup fails (in which
    case the caller should fall through to the normal LLM flow)."""
    if not text:
        return None
    if not any(kw in text for kw in _EARNINGS_KEYWORDS):
        return None
    if not any(kw in text for kw in _DATE_QUERY_KEYWORDS):
        return None

    ticker = extract_ticker(text)
    if ticker is None:
        return None
    if ticker in _EXCLUDED_TICKERS:
        return None
    if not _is_ticker_adjacent_to_earnings(text, ticker):
        return None

    today = date.today()
    date_from = today.isoformat()
    date_to = (today + timedelta(days=FAST_PATH_WINDOW_DAYS)).isoformat()

    try:
        rows = get_earnings_calendar(date_from, date_to, symbol=ticker)
    except Exception:
        return None

    return _format_answer(ticker, rows)
