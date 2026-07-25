"""Tool-level tests. HTTP tools mock httpx."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import agent.market_summary as market_summary_module
from agent.tools import ALL_TOOL_SCHEMAS, TOOL_FUNCTIONS
from agent.tools import _http as tools_http
from agent.tools import earnings, estimates, market_summary_tool, news, quote


@pytest.fixture
def mock_http(monkeypatch):
    client = MagicMock()
    monkeypatch.setenv("STORAGE_BACKEND", "dynamodb")
    monkeypatch.setattr(tools_http, "_client", client)
    monkeypatch.setattr(tools_http, "client", lambda: client)
    return client


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


# --- schemas ---

def test_all_tool_schemas_have_required_fields():
    names = {t["name"] for t in ALL_TOOL_SCHEMAS}
    assert names == {
        "get_earnings_calendar",
        "get_analyst_estimates",
        "get_stock_news",
        "get_quote",
        "get_market_summary",
    }
    for tool in ALL_TOOL_SCHEMAS:
        assert "description" in tool
        assert tool["input_schema"]["type"] == "object"


def test_tool_functions_match_schemas():
    schema_names = {t["name"] for t in ALL_TOOL_SCHEMAS}
    assert set(TOOL_FUNCTIONS.keys()) == schema_names


# --- earnings ---

def test_earnings_calls_finnhub_calendar(mock_http):
    mock_http.get.return_value = _resp({"earningsCalendar": [{"symbol": "AAPL"}]})
    out = earnings.get_earnings_calendar("2026-05-01", "2026-05-02")
    assert out == [{"symbol": "AAPL"}]
    args, kwargs = mock_http.get.call_args
    assert args[0] == "/calendar/earnings"
    assert kwargs["params"]["from"] == "2026-05-01"
    assert kwargs["params"]["to"] == "2026-05-02"
    assert "symbol" not in kwargs["params"]


def test_earnings_passes_symbol_to_finnhub(mock_http):
    mock_http.get.return_value = _resp({"earningsCalendar": [{"symbol": "AAPL"}]})
    earnings.get_earnings_calendar("2026-05-01", "2026-08-01", symbol="aapl")
    params = mock_http.get.call_args.kwargs["params"]
    assert params["symbol"] == "AAPL"


def test_earnings_filters_client_side_when_finnhub_returns_all(mock_http):
    mock_http.get.return_value = _resp(
        {"earningsCalendar": [{"symbol": "AAPL"}, {"symbol": "MSFT"}, {"symbol": "AAPL"}]}
    )
    out = earnings.get_earnings_calendar("2026-05-01", "2026-08-01", symbol="AAPL")
    assert all(it["symbol"] == "AAPL" for it in out)
    assert len(out) == 2


def test_get_company_industry_calls_profile2(mock_http):
    mock_http.get.return_value = _resp({"finnhubIndustry": "Technology"})
    out = earnings.get_company_industry("AAPL")
    assert out == "Technology"
    args, kwargs = mock_http.get.call_args
    assert args[0] == "/stock/profile2"
    assert kwargs["params"]["symbol"] == "AAPL"


def test_get_company_industry_returns_empty_when_missing(mock_http):
    mock_http.get.return_value = _resp({})
    assert earnings.get_company_industry("UNKNOWN") == ""


# --- estimates ---

def test_estimates_combines_recommendation_and_eps(mock_http):
    mock_http.get.side_effect = [
        _resp([{"period": "2026-04-01", "buy": 20, "hold": 5, "sell": 1, "strongBuy": 8, "strongSell": 0}]),
        _resp({"data": [{"period": "2026-06-30", "epsAvg": 1.5, "epsHigh": 1.8, "epsLow": 1.2, "numberAnalysts": 25}]}),
    ]
    out = estimates.get_analyst_estimates("AAPL")
    assert out["symbol"] == "AAPL"
    assert out["recommendation"]["buy"] == 20
    assert out["eps_estimates"][0]["eps_avg"] == 1.5


# --- news ---

def test_news_returns_top_5(mock_http):
    mock_http.get.return_value = _resp(
        [{"headline": f"news {i}", "summary": "...", "url": "u", "datetime": 0, "source": "s"} for i in range(10)]
    )
    out = news.get_stock_news("AAPL", days_back=3)
    assert len(out) == 5
    assert out[0]["headline"] == "news 0"


def test_news_clamps_days_back(mock_http):
    mock_http.get.return_value = _resp([])
    news.get_stock_news("AAPL", days_back=999)
    params = mock_http.get.call_args.kwargs["params"]
    # days_back was clamped to 30, so 'from' should be roughly today - 30.
    assert params["from"] < params["to"]


@pytest.mark.parametrize(
    "headline",
    [
        "Join us for our exclusive webinar on Q3 earnings",
        "Tune in this Friday for our live market roundtable",
        "Sign up now to get daily market alerts",
        "Register now for our free investing masterclass",
        "Most active S&P500 stocks in today's session — join us as we dive into today's biggest movers",
        "Stocks to watch this week",
        "Trending tickers on social media today",
        "Top gainers and losers in today's trading session",
    ],
)
def test_is_junk_headline_detects_marketing_and_listicle(headline):
    assert news.is_junk_headline(headline) is True


@pytest.mark.parametrize(
    "headline",
    [
        "Apple reports record Q3 revenue of $89.5 billion",
        "Tesla recalls 50,000 vehicles over battery defect",
        "Microsoft to acquire cybersecurity startup for $2B",
        "Fed raises interest rates by 25 basis points",
        # Regression cases: substring/singular-vs-plural false positives
        # flagged in review — these are real per-stock news, not marketing
        # listicles, and must not be filtered.
        "Netflix subscriber growth beats estimates in Q2",
        "Disney+ subscribers hit record high",
        "Tesla was the top gainer in the S&P 500 today after earnings beat",
        "Apple is a stock to watch after strong iPhone sales data",
        "AMC among most actively traded stocks as retail interest surges",
    ],
)
def test_is_junk_headline_allows_real_news(headline):
    assert news.is_junk_headline(headline) is False


def test_is_junk_headline_case_insensitive():
    assert news.is_junk_headline("JOIN US for a live session") is True
    assert news.is_junk_headline("SuBscRiBe to our newsletter today") is True


def test_is_junk_headline_handles_empty_and_none():
    assert news.is_junk_headline(None) is False
    assert news.is_junk_headline("") is False


def test_news_filters_out_junk_headlines(mock_http):
    mock_http.get.return_value = _resp([
        {"headline": "Apple reports record Q3 revenue", "summary": "...", "url": "u", "datetime": 0, "source": "s"},
        {"headline": "Stocks to watch this week", "summary": "...", "url": "u", "datetime": 0, "source": "s"},
        {"headline": "Join us for our free webinar", "summary": "...", "url": "u", "datetime": 0, "source": "s"},
        {"headline": "Tesla recalls vehicles over defect", "summary": "...", "url": "u", "datetime": 0, "source": "s"},
    ])
    out = news.get_stock_news("AAPL", days_back=3)
    headlines = [it["headline"] for it in out]
    assert headlines == ["Apple reports record Q3 revenue", "Tesla recalls vehicles over defect"]


def test_news_all_junk_returns_empty(mock_http):
    mock_http.get.return_value = _resp([
        {"headline": "Join us for our webinar", "summary": "...", "url": "u", "datetime": 0, "source": "s"},
        {"headline": "Stocks to watch this week", "summary": "...", "url": "u", "datetime": 0, "source": "s"},
    ])
    out = news.get_stock_news("AAPL", days_back=3)
    assert out == []


def test_news_all_real_returns_unfiltered(mock_http):
    mock_http.get.return_value = _resp(
        [{"headline": f"Company reports event {i}", "summary": "...", "url": "u", "datetime": 0, "source": "s"} for i in range(5)]
    )
    out = news.get_stock_news("AAPL", days_back=3)
    assert len(out) == 5


# --- quote ---

def test_get_quote_returns_normalized_fields(mock_http):
    mock_http.get.return_value = _resp(
        {"c": 150.0, "d": 2.0, "dp": 1.35, "h": 151.0, "l": 148.0, "o": 149.0, "pc": 148.0}
    )
    out = quote.get_quote("aapl")
    assert out == {
        "symbol": "AAPL",
        "current_price": 150.0,
        "change": 2.0,
        "change_percent": 1.35,
        "day_high": 151.0,
        "day_low": 148.0,
        "open_price": 149.0,
        "prev_close": 148.0,
    }
    args, kwargs = mock_http.get.call_args
    assert args[0] == "/quote"
    assert kwargs["params"]["symbol"] == "AAPL"


# --- market summary ---

def test_get_market_summary_returns_only_summary(monkeypatch):
    monkeypatch.setattr(
        market_summary_module,
        "generate_market_summary",
        lambda days_back: {
            "summary": "今日市場摘要文字",
            "news": {"big7": [{"headline": "h"}], "ai": [{"headline": "h2"}]},
            "indexes": [{"name": "S&P 500", "symbol": "SPY", "close": 500.0, "change_percent": 1.0}],
        },
    )
    out = market_summary_tool.get_market_summary()
    assert out == {"summary": "今日市場摘要文字"}
    assert "news" not in out
    assert "indexes" not in out


def test_get_market_summary_passes_days_back(monkeypatch):
    calls = []

    def fake_generate(days_back):
        calls.append(days_back)
        return {"summary": "s", "news": {"big7": [], "ai": []}, "indexes": []}

    monkeypatch.setattr(market_summary_module, "generate_market_summary", fake_generate)
    market_summary_tool.get_market_summary(days_back=3)
    assert calls == [3]


def test_get_market_summary_default_days_back_is_one(monkeypatch):
    calls = []

    def fake_generate(days_back):
        calls.append(days_back)
        return {"summary": "s", "news": {"big7": [], "ai": []}, "indexes": []}

    monkeypatch.setattr(market_summary_module, "generate_market_summary", fake_generate)
    market_summary_tool.get_market_summary()
    assert calls == [1]
