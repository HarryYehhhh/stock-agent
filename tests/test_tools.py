"""Tool-level tests. HTTP tools mock httpx."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from agent.tools import ALL_TOOL_SCHEMAS, TOOL_FUNCTIONS
from agent.tools import _http as tools_http
from agent.tools import earnings, estimates, news


@pytest.fixture
def mock_http(monkeypatch):
    client = MagicMock()
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
    assert names == {"get_earnings_calendar", "get_analyst_estimates", "get_stock_news"}
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

