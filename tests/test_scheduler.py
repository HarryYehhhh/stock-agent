"""Scheduler handler tests: fixed watchlist daily earnings push."""

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
    fake_get_earnings = MagicMock()
    fake_get_subs = MagicMock()
    fake_run_agent = MagicMock(return_value="summary text")
    fake_send = MagicMock()

    monkeypatch.setattr(
        scheduler_handler,
        "get_watchlist",
        lambda: [
            {"exchange": "NASDAQGS", "ticker": "AAPL"},
            {"exchange": "NASDAQGS", "ticker": "NVDA"},
            {"exchange": "NASDAQGM", "ticker": "QQQ"},
        ],
    )
    monkeypatch.setattr(scheduler_handler, "get_earnings_calendar", fake_get_earnings)
    monkeypatch.setattr(scheduler_handler, "get_all_active", fake_get_subs)
    monkeypatch.setattr(scheduler_handler, "run_agent", fake_run_agent)
    monkeypatch.setattr(scheduler_handler, "send_message", fake_send)

    return {
        "earnings": fake_get_earnings,
        "subs": fake_get_subs,
        "agent": fake_run_agent,
        "send": fake_send,
    }


def test_skips_when_watchlist_has_no_earnings(mocks):
    mocks["earnings"].return_value = []
    assert scheduler_handler.handler({}, CTX) == {"statusCode": 200}
    assert [call.kwargs["symbol"] for call in mocks["earnings"].call_args_list] == [
        "AAPL",
        "NVDA",
        "QQQ",
    ]
    mocks["agent"].assert_not_called()
    mocks["send"].assert_not_called()


def test_pushes_only_watchlist_symbols_with_earnings(mocks):
    def earnings(_date_from, _date_to, symbol):
        if symbol == "AAPL":
            return [{"symbol": "AAPL", "hour": "amc"}]
        if symbol == "NVDA":
            return [{"symbol": "NVDA", "hour": "bmo"}]
        return []

    mocks["earnings"].side_effect = earnings
    mocks["subs"].return_value = [111, 222]

    scheduler_handler.handler({}, CTX)

    prompt = mocks["agent"].call_args.args[0]
    assert "AAPL" in prompt
    assert "NVDA" in prompt
    assert "QQQ" not in prompt
    assert "NASDAQGS" in prompt
    assert "固定追蹤清單" in prompt
    assert "不要自行挑選或省略" in prompt
    assert "5-10" not in prompt

    assert mocks["send"].call_count == 2


def test_skips_when_no_subscribers(mocks):
    def earnings(_date_from, _date_to, symbol):
        return [{"symbol": symbol}]

    mocks["earnings"].side_effect = earnings
    mocks["subs"].return_value = []

    scheduler_handler.handler({}, CTX)

    mocks["agent"].assert_not_called()
    mocks["send"].assert_not_called()


def test_symbol_lookup_failure_does_not_stop_other_watchlist_symbols(mocks):
    def earnings(_date_from, _date_to, symbol):
        if symbol == "AAPL":
            raise RuntimeError("boom")
        if symbol == "NVDA":
            return [{"symbol": "NVDA"}]
        return []

    mocks["earnings"].side_effect = earnings
    mocks["subs"].return_value = [111]
    scheduler_handler.handler({}, CTX)

    prompt = mocks["agent"].call_args.args[0]
    assert "NVDA" in prompt
    assert "AAPL" not in prompt
