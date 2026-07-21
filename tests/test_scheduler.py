"""Scheduler handler tests: fixed watchlist daily earnings push."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from agent import scheduler_handler


class _Ctx:
    function_name = "test"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:test"
    aws_request_id = "req-1"


CTX = _Ctx()

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


@pytest.fixture
def mocks(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "dynamodb")

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


@pytest.fixture
def local_mocks(local_store, monkeypatch):
    from agent.db.backend import get_backend

    fake_get_subs = MagicMock(return_value=["111"])
    fake_run_agent = MagicMock(return_value="summary text")
    fake_send = MagicMock()

    monkeypatch.setattr(
        scheduler_handler,
        "get_watchlist",
        lambda: [{"exchange": "NASDAQGS", "ticker": "AAPL"}],
    )
    monkeypatch.setattr(scheduler_handler, "get_all_active", fake_get_subs)
    monkeypatch.setattr(scheduler_handler, "run_agent", fake_run_agent)
    monkeypatch.setattr(scheduler_handler, "send_message", fake_send)

    def sync_ok(_date_from, _date_to, symbol):
        # Mimics the real tool: persists fetched rows into the local store.
        items = [
            {
                "symbol": symbol,
                "date": TOMORROW,
                "hour": "amc",
                "epsEstimate": 1.4,
                "revenueEstimate": 82000000000,
            }
        ]
        get_backend().upsert_earnings_calendar(items)
        return items

    return {
        "subs": fake_get_subs,
        "agent": fake_run_agent,
        "send": fake_send,
        "sync_ok": sync_ok,
        "backend": get_backend,
    }


def test_local_pushes_from_sqlite_join_and_is_idempotent(local_mocks, monkeypatch):
    monkeypatch.setattr(
        scheduler_handler, "get_earnings_calendar", local_mocks["sync_ok"]
    )

    scheduler_handler.handler({}, CTX)

    prompt = local_mocks["agent"].call_args.args[0]
    assert "AAPL" in prompt
    assert "NASDAQGS" in prompt
    body = local_mocks["send"].call_args.args[1]
    assert "⚠️" not in body
    assert local_mocks["send"].call_count == 1

    # Rerun: everything already notified, no duplicate push.
    scheduler_handler.handler({}, CTX)
    assert local_mocks["send"].call_count == 1
    assert local_mocks["agent"].call_count == 1


def test_local_warns_and_uses_cache_when_finnhub_fails(local_mocks, monkeypatch):
    backend = local_mocks["backend"]()
    backend.upsert_earnings_calendar(
        [{"symbol": "AAPL", "date": TOMORROW, "hour": "bmo", "epsEstimate": 1.5}]
    )

    def sync_fail(_date_from, _date_to, symbol):
        raise RuntimeError("finnhub down")

    monkeypatch.setattr(scheduler_handler, "get_earnings_calendar", sync_fail)

    scheduler_handler.handler({}, CTX)

    assert local_mocks["send"].call_count == 1
    body = local_mocks["send"].call_args.args[1]
    assert "⚠️" in body
    assert "最後同步" in body
    assert "1 檔查詢失敗" in body


def test_local_skips_when_sync_fails_and_cache_is_empty(local_mocks, monkeypatch):
    def sync_fail(_date_from, _date_to, symbol):
        raise RuntimeError("finnhub down")

    monkeypatch.setattr(scheduler_handler, "get_earnings_calendar", sync_fail)

    scheduler_handler.handler({}, CTX)

    local_mocks["agent"].assert_not_called()
    local_mocks["send"].assert_not_called()


def test_local_join_ignores_non_watchlist_symbols(local_mocks, monkeypatch):
    from agent.db.backend import get_backend

    def sync_with_extra(_date_from, _date_to, symbol):
        items = [
            {"symbol": symbol, "date": TOMORROW, "hour": "amc"},
            {"symbol": "ZZZZ", "date": TOMORROW, "hour": "amc"},
        ]
        get_backend().upsert_earnings_calendar(items)
        return items

    monkeypatch.setattr(
        scheduler_handler, "get_earnings_calendar", sync_with_extra
    )

    scheduler_handler.handler({}, CTX)

    prompt = local_mocks["agent"].call_args.args[0]
    assert "AAPL" in prompt
    assert "ZZZZ" not in prompt


def test_local_send_failure_allows_retry_on_next_run(local_mocks, monkeypatch):
    monkeypatch.setattr(
        scheduler_handler, "get_earnings_calendar", local_mocks["sync_ok"]
    )
    local_mocks["send"].side_effect = RuntimeError("telegram down")

    scheduler_handler.handler({}, CTX)
    assert local_mocks["send"].call_count == 1

    # Next run retries the failed chat because nothing was marked notified.
    local_mocks["send"].side_effect = None
    scheduler_handler.handler({}, CTX)
    assert local_mocks["send"].call_count == 2
