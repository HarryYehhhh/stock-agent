"""Tool-level tests. HTTP tools mock httpx."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import agent.market_summary as market_summary_module
from agent.tools import ALL_TOOL_SCHEMAS, TOOL_FUNCTIONS
from agent.tools import _http as tools_http
from agent.tools import earnings, estimates, knowledge_base_tool, market_summary_tool, news, quote


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
        "search_knowledge_base",
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


def test_earnings_calendar_strips_non_whitelisted_fields(mock_http, monkeypatch):
    fake_backend = MagicMock()
    monkeypatch.setattr(earnings, "get_backend", lambda: fake_backend)
    raw_row = {
        "symbol": "AAPL",
        "date": "2026-07-23",
        "hour": "amc",
        "epsEstimate": 1.42,
        "epsActual": None,
        "revenueEstimate": 82000000000,
        "revenueActual": None,
        "quarter": 3,
        "year": 2026,
        # Fields Finnhub returns that we don't want to forward to the LLM:
        "epsEstimateHigh": 1.6,
        "epsEstimateLow": 1.3,
        "epsEstimateAnalystsCount": 20,
        "revenueEstimateHigh": 90000000000,
        "revenueEstimateLow": 78000000000,
        "revenueEstimateAnalystsCount": 15,
    }
    mock_http.get.return_value = _resp({"earningsCalendar": [raw_row]})

    out = earnings.get_earnings_calendar("2026-07-20", "2026-07-24")

    assert len(out) == 1
    assert set(out[0].keys()) == set(earnings.EARNINGS_FIELDS)
    assert out[0]["symbol"] == "AAPL"
    assert out[0]["epsEstimate"] == 1.42


def test_earnings_calendar_caps_at_50_rows(mock_http, monkeypatch):
    fake_backend = MagicMock()
    monkeypatch.setattr(earnings, "get_backend", lambda: fake_backend)
    rows = [{"symbol": f"SYM{i}", "date": "2026-07-23"} for i in range(60)]
    mock_http.get.return_value = _resp({"earningsCalendar": rows})

    out = earnings.get_earnings_calendar("2026-07-20", "2026-07-24")

    assert len(out) == earnings.EARNINGS_ROW_LIMIT == 50


def test_select_rows_covers_every_date_and_ranks_by_revenue_desc_with_none_last():
    """>limit rows spread across multiple dates: truncation must not let one
    date (e.g. the alphabetically-first symbols) eat the whole budget. Every
    date keeps a slot, and within a date, higher revenueEstimate wins with
    missing values sorted last."""
    dates = ["2026-07-20", "2026-07-21", "2026-07-22"]
    items = []
    for date in dates:
        for i in range(20):
            # revenueEstimate descends 190..10 for i=0..17, None for i=18,19
            revenue = None if i >= 18 else (190 - i * 10)
            items.append({"symbol": f"{date}-SYM{i}", "date": date, "revenueEstimate": revenue})
    assert len(items) == 60

    out = earnings._select_rows(items, 50)

    assert len(out) <= 50
    by_date: dict[str, list[dict]] = {}
    for row in out:
        by_date.setdefault(row["date"], []).append(row)
    assert set(by_date) == set(dates), "every date in range must keep a representative"

    for date in dates:
        full_sorted = sorted(
            (it for it in items if it["date"] == date),
            key=lambda it: (it["revenueEstimate"] is None, -(it["revenueEstimate"] or 0)),
        )
        selected = by_date[date]
        # Selected rows for this date must be exactly the top-N by revenue
        # (descending, None last), in that same order.
        assert selected == full_sorted[: len(selected)]


def test_earnings_calendar_upserts_thinned_data(mock_http, monkeypatch):
    """Backend upsert only reads symbol/date/hour/epsEstimate/revenueEstimate
    (see db/backend.py upsert_earnings_calendar), a subset of EARNINGS_FIELDS,
    so it's safe to reuse the already-thinned rows instead of upserting the
    raw Finnhub payload."""
    fake_backend = MagicMock()
    monkeypatch.setattr(earnings, "get_backend", lambda: fake_backend)
    raw_row = {
        "symbol": "AAPL",
        "date": "2026-07-23",
        "hour": "amc",
        "epsEstimate": 1.42,
        "revenueEstimate": 82000000000,
        "surprisePercent": 5.2,  # not whitelisted, not needed by backend
    }
    mock_http.get.return_value = _resp({"earningsCalendar": [raw_row]})

    out = earnings.get_earnings_calendar("2026-07-20", "2026-07-24")

    fake_backend.upsert_earnings_calendar.assert_called_once_with(out)
    upserted = fake_backend.upsert_earnings_calendar.call_args[0][0]
    assert "surprisePercent" not in upserted[0]


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

def test_market_summary_tool_days_back_description_maps_time_phrases():
    description = market_summary_tool.MARKET_SUMMARY_TOOL["input_schema"]["properties"]["days_back"]["description"]

    assert "today" in description
    assert "本日" in description
    assert "-> 1" in description
    assert "本週" in description
    assert "-> 7" in description


def test_market_summary_tool_days_back_description_caps_longer_periods_at_7():
    """本月或更長期間也要傳 7（schema maximum），避免模型傳超過上限觸發 400。"""
    description = market_summary_tool.MARKET_SUMMARY_TOOL["input_schema"]["properties"]["days_back"]["description"]

    assert "本月" in description
    assert market_summary_tool.MARKET_SUMMARY_TOOL["input_schema"]["properties"]["days_back"]["maximum"] == 7


def test_get_market_summary_returns_only_summary(monkeypatch):
    monkeypatch.setattr(
        market_summary_module,
        "generate_market_summary",
        lambda days_back: {
            "summary": "今日市場摘要文字",
            "news": {"big7": [{"headline": "h"}], "ai": [{"headline": "h2"}]},
            "indexes": [{"name": "S&P 500", "symbol": "SPY", "close": 500.0, "change_percent": 1.0}],
            "date_range": "2026-07-25",
        },
    )
    out = market_summary_tool.get_market_summary()
    assert out == {"summary": "今日市場摘要文字", "date_range": "2026-07-25"}
    assert "news" not in out
    assert "indexes" not in out


def test_get_market_summary_passes_days_back(monkeypatch):
    calls = []

    def fake_generate(days_back):
        calls.append(days_back)
        return {"summary": "s", "news": {"big7": [], "ai": []}, "indexes": [], "date_range": None}

    monkeypatch.setattr(market_summary_module, "generate_market_summary", fake_generate)
    market_summary_tool.get_market_summary(days_back=3)
    assert calls == [3]


def test_get_market_summary_default_days_back_is_one(monkeypatch):
    calls = []

    def fake_generate(days_back):
        calls.append(days_back)
        return {"summary": "s", "news": {"big7": [], "ai": []}, "indexes": [], "date_range": None}

    monkeypatch.setattr(market_summary_module, "generate_market_summary", fake_generate)
    market_summary_tool.get_market_summary()
    assert calls == [1]


# --- knowledge base ---


def test_search_knowledge_base_returns_dict_with_results_and_source():
    out = knowledge_base_tool.search_knowledge_base("道瓊怎麼查")
    assert isinstance(out, dict)
    assert "results" in out
    assert out["results"], "expected at least one hit"
    top = out["results"][0]
    assert set(top.keys()) == {"source", "heading", "content"}
    assert top["source"].startswith("indices#")
    assert "DIA" in top["content"]


def test_search_knowledge_base_no_hit_returns_empty_results():
    out = knowledge_base_tool.search_knowledge_base("今天天氣如何")
    assert out == {"results": []}


def test_search_knowledge_base_default_top_k_is_three():
    out = knowledge_base_tool.search_knowledge_base("beat")
    assert len(out["results"]) <= 3


def test_search_knowledge_base_top_k_clamped_to_five():
    out = knowledge_base_tool.search_knowledge_base("beat", top_k=100)
    assert len(out["results"]) <= 5


def test_search_knowledge_base_top_k_one():
    out = knowledge_base_tool.search_knowledge_base("道瓊怎麼查", top_k=1)
    assert len(out["results"]) == 1


def test_search_knowledge_base_registered_in_tool_functions():
    assert "search_knowledge_base" in TOOL_FUNCTIONS
    out = TOOL_FUNCTIONS["search_knowledge_base"](query="道瓊怎麼查")
    assert isinstance(out, dict)
    assert "results" in out


def test_run_tool_passes_dict_result_through_unwrapped():
    """agent._run_tool wraps non-dict results in {"result": ...}; dicts pass through as-is."""
    from agent.agent import _run_tool

    out = _run_tool("search_knowledge_base", {"query": "道瓊怎麼查"})
    assert "result" not in out
    assert "results" in out


def test_no_circular_import_via_tools_package():
    """agent.knowledge.retrieval is stdlib-only; importing the tools package
    (which now includes knowledge_base_tool) must not trigger a circular
    import with agent.agent."""
    import agent.agent
    from agent.tools import TOOL_FUNCTIONS as tool_functions

    assert "search_knowledge_base" in tool_functions
    assert callable(agent.agent.run_agent)
