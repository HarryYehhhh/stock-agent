"""Scheduler handler tests: dynamodb full-pipeline handler, local notify flow."""

from __future__ import annotations

import sqlite3
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


# ---------------------------------------------------------------------------
# handler(): cloud (dynamodb) full pipeline — sync → notify → market summary
# ---------------------------------------------------------------------------


@pytest.fixture
def pipeline_mocks(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "dynamodb")
    fake_sync = MagicMock(return_value=[])
    fake_run = MagicMock()
    fake_push = MagicMock()

    monkeypatch.setattr(scheduler_handler, "sync_earnings", fake_sync)
    monkeypatch.setattr(scheduler_handler, "_run", fake_run)
    monkeypatch.setattr(scheduler_handler, "push_daily_summary", fake_push)

    return {"sync": fake_sync, "run": fake_run, "push": fake_push}


def test_handler_runs_all_three_stages_and_returns_200(pipeline_mocks):
    result = scheduler_handler.handler({}, CTX)

    assert result == {"statusCode": 200}
    pipeline_mocks["sync"].assert_called_once()
    pipeline_mocks["run"].assert_called_once()
    pipeline_mocks["push"].assert_called_once()


def test_handler_stage_failure_does_not_block_other_stages_but_raises(pipeline_mocks):
    pipeline_mocks["run"].side_effect = RuntimeError("notify boom")

    with pytest.raises(Exception):
        scheduler_handler.handler({}, CTX)

    pipeline_mocks["sync"].assert_called_once()
    pipeline_mocks["run"].assert_called_once()
    pipeline_mocks["push"].assert_called_once()


def test_notify_live_removed():
    """Regression: `_notify_live` (ADR-0006 §2) no longer exists."""
    assert not hasattr(scheduler_handler, "_notify_live")


# ---------------------------------------------------------------------------
# Local backend: sync cron + cached per-company summaries
# ---------------------------------------------------------------------------


def _seed_earnings(symbols: list[str]) -> None:
    from agent.db.backend import get_backend

    get_backend().upsert_earnings_calendar(
        [
            {
                "symbol": symbol,
                "date": TOMORROW,
                "hour": "amc",
                "epsEstimate": 1.4,
                "revenueEstimate": 82000000000,
            }
            for symbol in symbols
        ]
    )


@pytest.fixture
def local_mocks(local_store, monkeypatch):
    from agent.db.subscribers import subscribe
    from agent.db.watchlist import add_to_watchlist

    fake_run_agent = MagicMock(return_value="*AAPL* 財報摘要")
    fake_send = MagicMock()

    subscribe("111")
    add_to_watchlist("111", "AAPL")

    monkeypatch.setattr(scheduler_handler, "run_agent", fake_run_agent)
    monkeypatch.setattr(scheduler_handler, "send_message", fake_send)

    return {"agent": fake_run_agent, "send": fake_send}


def test_sync_earnings_fetches_active_watchlist_symbols(local_mocks, monkeypatch):
    from agent.db.subscribers import subscribe
    from agent.db.watchlist import add_to_watchlist

    subscribe("222")
    add_to_watchlist("222", "NVDA")
    add_to_watchlist("222", "AAPL")  # shared with 111 → fetched once

    fake_fetch = MagicMock(return_value=[])
    monkeypatch.setattr(scheduler_handler, "get_earnings_calendar", fake_fetch)

    failed = scheduler_handler.sync_earnings()

    assert failed == []
    fetched = sorted(call.kwargs["symbol"] for call in fake_fetch.call_args_list)
    assert fetched == ["AAPL", "NVDA"]


def test_sync_earnings_collects_failures(local_mocks, monkeypatch):
    def flaky(_date_from, _date_to, symbol):
        raise RuntimeError("finnhub down")

    monkeypatch.setattr(scheduler_handler, "get_earnings_calendar", flaky)

    assert scheduler_handler.sync_earnings() == ["AAPL"]


def test_local_notify_reads_sqlite_only_and_is_idempotent(local_mocks):
    _seed_earnings(["AAPL"])

    scheduler_handler._run()

    prompt = local_mocks["agent"].call_args.args[0]
    assert "AAPL" in prompt
    assert local_mocks["send"].call_args.args[0] == "111"
    body = local_mocks["send"].call_args.args[1]
    assert "⚠️" not in body
    assert "*AAPL* 財報摘要" in body
    assert local_mocks["send"].call_count == 1

    # Rerun: already notified, no duplicate push and no extra Groq call.
    scheduler_handler._run()
    assert local_mocks["send"].call_count == 1
    assert local_mocks["agent"].call_count == 1


def test_local_company_summary_cached_across_subscribers(local_mocks, local_store):
    from agent.db.subscribers import subscribe
    from agent.db.watchlist import add_to_watchlist

    subscribe("222")
    add_to_watchlist("222", "AAPL")
    _seed_earnings(["AAPL"])

    scheduler_handler._run()

    # Two subscribers pushed, but the AAPL summary was generated only once.
    assert local_mocks["send"].call_count == 2
    assert local_mocks["agent"].call_count == 1

    with sqlite3.connect(local_store) as conn:
        rows = conn.execute(
            "SELECT symbol, earning_date, summary FROM company_summaries"
        ).fetchall()
    assert rows == [("AAPL", TOMORROW, "*AAPL* 財報摘要")]


def test_local_warns_when_cache_is_stale(local_mocks, local_store):
    _seed_earnings(["AAPL"])
    with sqlite3.connect(local_store) as conn:
        conn.execute(
            "UPDATE earnings_calendar SET updated_at = '2020-01-01T00:00:00+00:00'"
        )

    scheduler_handler._run()

    body = local_mocks["send"].call_args.args[1]
    assert "⚠️" in body
    assert "最後同步" in body
    assert "2020-01-01" in body


def test_local_skips_when_no_cached_earnings(local_mocks):
    scheduler_handler._run()

    local_mocks["agent"].assert_not_called()
    local_mocks["send"].assert_not_called()


def test_local_summary_failure_falls_back_and_retries_next_run(local_mocks, local_store):
    _seed_earnings(["AAPL"])
    local_mocks["agent"].side_effect = RuntimeError("groq down")

    scheduler_handler._run()

    body = local_mocks["send"].call_args.args[1]
    assert "摘要生成失敗" in body
    assert "AAPL" in body
    # Fallback text is not cached, so a later run regenerates the summary.
    with sqlite3.connect(local_store) as conn:
        assert conn.execute("SELECT COUNT(*) FROM company_summaries").fetchone()[0] == 0


def test_local_send_failure_allows_retry_and_reuses_cached_summary(local_mocks):
    _seed_earnings(["AAPL"])
    local_mocks["send"].side_effect = RuntimeError("telegram down")

    scheduler_handler._run()
    assert local_mocks["send"].call_count == 1

    # Next run retries the failed chat from the cached summary (no new Groq call).
    local_mocks["send"].side_effect = None
    scheduler_handler._run()
    assert local_mocks["send"].call_count == 2
    assert local_mocks["agent"].call_count == 1


def test_local_per_subscriber_push_only_own_symbols(local_mocks):
    from agent.db.subscribers import subscribe
    from agent.db.watchlist import add_to_watchlist

    subscribe("222")
    add_to_watchlist("222", "NVDA")
    _seed_earnings(["AAPL", "NVDA", "ZZZZ"])

    def summary_for(prompt, _history):
        return "*NVDA* 摘要" if "NVDA" in prompt else "*AAPL* 摘要"

    local_mocks["agent"].side_effect = summary_for

    scheduler_handler._run()

    assert local_mocks["send"].call_count == 2
    bodies = {call.args[0]: call.args[1] for call in local_mocks["send"].call_args_list}
    assert "*AAPL* 摘要" in bodies["111"] and "NVDA" not in bodies["111"]
    assert "*NVDA* 摘要" in bodies["222"] and "AAPL" not in bodies["222"]
    assert all("ZZZZ" not in body for body in bodies.values())


def test_local_inactive_subscriber_is_excluded_from_push(local_mocks):
    from agent.db.subscribers import unsubscribe

    unsubscribe("111")
    _seed_earnings(["AAPL"])

    scheduler_handler._run()

    local_mocks["agent"].assert_not_called()
    local_mocks["send"].assert_not_called()
