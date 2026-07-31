"""E2E integration tests for flow A (local backend / SQLite):
/subscribe -> /watch -> sync_earnings() -> notify.

Drives the real command routing (processor_handler._process_update) and the
real scheduler entry points (sync_earnings / _run, the notify step used by
`handler()`); only the outer edges (Finnhub HTTP, Groq, Telegram) are mocked.
Assertions read directly from the tmp SQLite store to confirm the full chain
actually persisted data.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from unittest.mock import MagicMock

from agent import processor_handler, scheduler_handler
from agent.db.watchlist import DEFAULT_WATCHLIST, remove_from_watchlist
from agent.tools import _http as tools_http

# `_run()` notifies on earnings releasing within the next 24 hours, keyed by
# today's (UTC) date — see scheduler_handler._run().
TARGET_DATE = date.today().isoformat()


def _process(chat_id: str, text: str) -> None:
    processor_handler._process_update({"message": {"chat": {"id": chat_id}, "text": text}})


def _only_watch(chat_id: str, symbol: str) -> None:
    """Trim chat_id's watchlist down to a single default symbol (removes the rest)."""
    for item in DEFAULT_WATCHLIST:
        if item["symbol"] != symbol:
            remove_from_watchlist(chat_id, item["symbol"])


def _row(symbol: str, day: str) -> dict:
    return {
        "symbol": symbol,
        "date": day,
        "hour": "amc",
        "epsEstimate": 1.23,
        "revenueEstimate": 456000000,
    }


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeFinnhubClient:
    """Socketless stand-in for the module-level httpx client in agent.tools._http."""

    def __init__(self):
        self._rows: dict[str, list[dict]] = {}

    def register(self, symbol: str, rows: list[dict]) -> None:
        self._rows[symbol.upper()] = rows

    def get(self, path: str, params: dict | None = None, **_kwargs) -> _FakeResponse:
        symbol = (params or {}).get("symbol", "")
        return _FakeResponse({"earningsCalendar": self._rows.get(symbol.upper(), [])})


def _mocks(monkeypatch, run_agent_result="*STUB* 財報摘要"):
    """Patch the three external boundaries: Finnhub client, Groq, Telegram."""
    fake_finnhub = _FakeFinnhubClient()
    monkeypatch.setattr(tools_http, "_client", fake_finnhub)
    monkeypatch.setattr(processor_handler, "send_message", MagicMock())
    fake_send = MagicMock()
    monkeypatch.setattr(scheduler_handler, "send_message", fake_send)
    fake_run_agent = MagicMock(return_value=run_agent_result)
    monkeypatch.setattr(scheduler_handler, "run_agent", fake_run_agent)
    return fake_finnhub, fake_send, fake_run_agent


def test_full_chain_subscribe_watch_sync_notify(local_store, monkeypatch):
    fake_finnhub, fake_send, fake_run_agent = _mocks(
        monkeypatch, run_agent_result="*PLTR* 財報摘要"
    )

    _process("111", "/subscribe")
    _process("111", "/watch PLTR")  # PLTR is not in DEFAULT_WATCHLIST
    fake_finnhub.register("PLTR", [_row("PLTR", TARGET_DATE)])

    assert scheduler_handler.sync_earnings() == []

    with sqlite3.connect(local_store) as conn:
        assert conn.execute(
            "SELECT chat_id, active FROM subscribers"
        ).fetchall() == [("111", 1)]
        assert conn.execute(
            "SELECT symbol FROM watchlist WHERE chat_id = ?", ("111",)
        ).fetchall(), "watchlist should be seeded"
        assert conn.execute(
            "SELECT 1 FROM watchlist WHERE chat_id = ? AND symbol = ?", ("111", "PLTR")
        ).fetchone() is not None
        assert conn.execute(
            "SELECT symbol, earning_date FROM earnings_calendar WHERE symbol = ?", ("PLTR",)
        ).fetchall() == [("PLTR", TARGET_DATE)]

    scheduler_handler._run()

    assert fake_send.call_count == 1
    call = fake_send.call_args_list[0]
    assert call.args[0] == "111"
    body = call.args[1]
    assert "PLTR" in body
    assert "AAPL" not in body

    with sqlite3.connect(local_store) as conn:
        assert conn.execute(
            "SELECT chat_id, symbol, earning_date FROM earnings_notifications"
        ).fetchall() == [("111", "PLTR", TARGET_DATE)]


def test_notify_rerun_is_idempotent(local_store, monkeypatch):
    fake_finnhub, fake_send, fake_run_agent = _mocks(
        monkeypatch, run_agent_result="*PLTR* 財報摘要"
    )

    _process("111", "/subscribe")
    _process("111", "/watch PLTR")
    fake_finnhub.register("PLTR", [_row("PLTR", TARGET_DATE)])
    scheduler_handler.sync_earnings()

    scheduler_handler._run()
    assert fake_send.call_count == 1
    assert fake_run_agent.call_count == 1

    scheduler_handler._run()
    assert fake_send.call_count == 1
    assert fake_run_agent.call_count == 1

    with sqlite3.connect(local_store) as conn:
        assert conn.execute(
            "SELECT chat_id, symbol, earning_date FROM earnings_notifications"
        ).fetchall() == [("111", "PLTR", TARGET_DATE)]


def test_per_subscriber_gets_only_own_watched_symbols(local_store, monkeypatch):
    fake_finnhub, fake_send, fake_run_agent = _mocks(monkeypatch)

    def summary_for(prompt, _history):
        return "*NVDA* 摘要" if "NVDA" in prompt else "*AAPL* 摘要"

    fake_run_agent.side_effect = summary_for

    _process("111", "/subscribe")
    _process("222", "/subscribe")
    _only_watch("111", "AAPL")
    _only_watch("222", "NVDA")
    fake_finnhub.register("AAPL", [_row("AAPL", TARGET_DATE)])
    fake_finnhub.register("NVDA", [_row("NVDA", TARGET_DATE)])

    scheduler_handler.sync_earnings()
    scheduler_handler._run()

    assert fake_send.call_count == 2
    bodies = {call.args[0]: call.args[1] for call in fake_send.call_args_list}
    assert "*AAPL* 摘要" in bodies["111"] and "NVDA" not in bodies["111"]
    assert "*NVDA* 摘要" in bodies["222"] and "AAPL" not in bodies["222"]


def test_unsubscribed_user_not_notified(local_store, monkeypatch):
    fake_finnhub, fake_send, fake_run_agent = _mocks(monkeypatch)

    _process("111", "/subscribe")
    _process("111", "/watch AAPL")
    _process("111", "/unsubscribe")
    fake_finnhub.register("AAPL", [_row("AAPL", TARGET_DATE)])

    assert scheduler_handler.sync_earnings() == []
    scheduler_handler._run()

    fake_run_agent.assert_not_called()
    fake_send.assert_not_called()


def test_no_earnings_today_no_push(local_store, monkeypatch):
    fake_finnhub, fake_send, fake_run_agent = _mocks(monkeypatch)

    _process("111", "/subscribe")
    _process("111", "/watch AAPL")
    # No symbol registered with fake_finnhub -> everything comes back empty.

    scheduler_handler.sync_earnings()
    scheduler_handler._run()

    fake_run_agent.assert_not_called()
    fake_send.assert_not_called()
