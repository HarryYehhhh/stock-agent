"""Scheduler handler tests: tech-only filtering of daily earnings push."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent import scheduler_handler


class _Ctx:
    function_name = "test"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:test"
    aws_request_id = "req-1"


CTX = _Ctx()


@pytest.fixture
def mocks(monkeypatch):
    industries = {
        "AAPL": "Technology",
        "NVDA": "Semiconductors",
        "MSFT": "Software",
        "JPM": "Banking",
        "XOM": "Oil & Gas",
    }
    calls = {}

    def fake_industry(symbol):
        calls.setdefault("industry", []).append(symbol)
        return industries.get(symbol, "Unknown")

    fake_get_earnings = MagicMock()
    fake_get_subs = MagicMock()
    fake_run_agent = MagicMock(return_value="summary text")
    fake_send = MagicMock()

    monkeypatch.setattr(scheduler_handler, "get_earnings_calendar", fake_get_earnings)
    monkeypatch.setattr(scheduler_handler, "get_company_industry", fake_industry)
    monkeypatch.setattr(scheduler_handler, "get_all_active", fake_get_subs)
    monkeypatch.setattr(scheduler_handler, "run_agent", fake_run_agent)
    monkeypatch.setattr(scheduler_handler, "send_message", fake_send)

    return {
        "earnings": fake_get_earnings,
        "subs": fake_get_subs,
        "agent": fake_run_agent,
        "send": fake_send,
        "industry_calls": calls,
    }


def test_skips_when_no_earnings(mocks):
    mocks["earnings"].return_value = []
    assert scheduler_handler.handler({}, CTX) == {"statusCode": 200}
    mocks["agent"].assert_not_called()
    mocks["send"].assert_not_called()


def test_filters_non_tech_stocks(mocks):
    mocks["earnings"].return_value = [
        {"symbol": "AAPL"},
        {"symbol": "JPM"},
        {"symbol": "NVDA"},
        {"symbol": "XOM"},
    ]
    mocks["subs"].return_value = [111, 222]

    scheduler_handler.handler({}, CTX)

    prompt = mocks["agent"].call_args.args[0]
    assert "AAPL" in prompt
    assert "NVDA" in prompt
    assert "JPM" not in prompt
    assert "XOM" not in prompt
    assert "科技類股" in prompt
    assert "Technology" in prompt
    assert "Semiconductors" in prompt

    assert mocks["send"].call_count == 2


def test_skips_push_when_all_filtered_out(mocks):
    mocks["earnings"].return_value = [{"symbol": "JPM"}, {"symbol": "XOM"}]

    assert scheduler_handler.handler({}, CTX) == {"statusCode": 200}

    mocks["subs"].assert_not_called()
    mocks["agent"].assert_not_called()
    mocks["send"].assert_not_called()


def test_skips_when_no_subscribers(mocks):
    mocks["earnings"].return_value = [{"symbol": "AAPL"}]
    mocks["subs"].return_value = []

    scheduler_handler.handler({}, CTX)

    mocks["agent"].assert_not_called()
    mocks["send"].assert_not_called()


def test_industry_lookup_failure_treated_as_non_tech(mocks, monkeypatch):
    mocks["earnings"].return_value = [{"symbol": "BOOM"}, {"symbol": "AAPL"}]
    mocks["subs"].return_value = [111]

    def industry(symbol):
        if symbol == "BOOM":
            raise RuntimeError("boom")
        return "Technology"

    monkeypatch.setattr(scheduler_handler, "get_company_industry", industry)

    scheduler_handler.handler({}, CTX)

    prompt = mocks["agent"].call_args.args[0]
    assert "AAPL" in prompt
    assert "BOOM" not in prompt
