"""Scheduler handler tests: dynamodb full-pipeline handler, local notify flow."""

from __future__ import annotations

import sqlite3
from datetime import date
from unittest.mock import MagicMock

import pytest

from agent import scheduler_handler


class _Ctx:
    function_name = "test"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:test"
    aws_request_id = "req-1"


CTX = _Ctx()

# `_run()` notifies on earnings releasing within the next 24 hours, keyed by
# today's (UTC) date — see scheduler_handler._run().
TARGET_DATE = date.today().isoformat()


# ---------------------------------------------------------------------------
# handler(): cloud (dynamodb) mode dispatch — sync vs notify (ADR-0007)
# ---------------------------------------------------------------------------


@pytest.fixture
def pipeline_mocks(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "dynamodb")
    fake_sync = MagicMock(return_value=[])
    fake_refresh_news = MagicMock(return_value=0)
    fake_run = MagicMock()
    fake_push = MagicMock()

    monkeypatch.setattr(scheduler_handler, "sync_earnings", fake_sync)
    monkeypatch.setattr(scheduler_handler, "refresh_news", fake_refresh_news)
    monkeypatch.setattr(scheduler_handler, "_run", fake_run)
    monkeypatch.setattr(scheduler_handler, "push_daily_summary", fake_push)

    return {
        "sync": fake_sync,
        "refresh_news": fake_refresh_news,
        "run": fake_run,
        "push": fake_push,
    }


def test_handler_sync_mode_runs_only_fetch_write_stages(pipeline_mocks):
    result = scheduler_handler.handler({"mode": "sync"}, CTX)

    assert result == {"statusCode": 200}
    pipeline_mocks["sync"].assert_called_once()
    pipeline_mocks["refresh_news"].assert_called_once_with(days_back=1)
    pipeline_mocks["run"].assert_not_called()
    pipeline_mocks["push"].assert_not_called()


def test_handler_notify_mode_runs_only_notify_stages_without_refresh(pipeline_mocks):
    result = scheduler_handler.handler({"mode": "notify"}, CTX)

    assert result == {"statusCode": 200}
    pipeline_mocks["sync"].assert_not_called()
    pipeline_mocks["refresh_news"].assert_not_called()
    pipeline_mocks["run"].assert_called_once()
    pipeline_mocks["push"].assert_called_once_with(refresh=False)


@pytest.mark.parametrize("event", [{}, {"mode": "foo"}, {"mode": None}])
def test_handler_missing_or_unknown_mode_raises_before_any_stage(pipeline_mocks, event):
    with pytest.raises(ValueError):
        scheduler_handler.handler(event, CTX)

    pipeline_mocks["sync"].assert_not_called()
    pipeline_mocks["refresh_news"].assert_not_called()
    pipeline_mocks["run"].assert_not_called()
    pipeline_mocks["push"].assert_not_called()


def test_handler_sync_mode_stage_failure_does_not_block_other_stage_but_raises(pipeline_mocks):
    pipeline_mocks["sync"].side_effect = RuntimeError("sync boom")

    with pytest.raises(Exception):
        scheduler_handler.handler({"mode": "sync"}, CTX)

    pipeline_mocks["sync"].assert_called_once()
    pipeline_mocks["refresh_news"].assert_called_once_with(days_back=1)


def test_handler_notify_mode_stage_failure_does_not_block_other_stage_but_raises(pipeline_mocks):
    pipeline_mocks["run"].side_effect = RuntimeError("notify boom")

    with pytest.raises(Exception):
        scheduler_handler.handler({"mode": "notify"}, CTX)

    pipeline_mocks["run"].assert_called_once()
    pipeline_mocks["push"].assert_called_once_with(refresh=False)


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
                "date": TARGET_DATE,
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


def test_sync_earnings_deletes_stale_rows_when_finnhub_moves_the_date(local_mocks, monkeypatch):
    """Regression: CRCL cached 8/18 by an earlier sync, then Finnhub moves the
    date to 8/5 — the stale 8/18 row must be cleaned up by sync_earnings()."""
    from agent.db.backend import get_backend

    today = date.today().isoformat()
    backend = get_backend()
    backend.upsert_earnings_calendar([{"symbol": "AAPL", "date": today}])

    def fake_fetch(_date_from, _date_to, symbol):
        # Mimics the real tool: the latest Finnhub fetch also persists the
        # new row (see tools.earnings.get_earnings_calendar).
        rows = [{"symbol": symbol, "date": "2026-12-31"}]
        backend.upsert_earnings_calendar(rows)
        return rows

    monkeypatch.setattr(scheduler_handler, "get_earnings_calendar", fake_fetch)

    failed = scheduler_handler.sync_earnings()

    assert failed == []
    rows = get_backend().get_chat_watchlist_earnings("111", "2020-01-01", "2030-12-31")
    assert [row["earning_date"] for row in rows] == ["2026-12-31"]


def test_sync_earnings_cleanup_failure_does_not_block_other_symbols(local_mocks, monkeypatch):
    """A single symbol's cleanup failure must not abort the sync of other
    symbols (same per-symbol resilience as the existing fetch-failure test)."""
    from agent.db.backend import get_backend

    fake_fetch = MagicMock(return_value=[])
    monkeypatch.setattr(scheduler_handler, "get_earnings_calendar", fake_fetch)
    monkeypatch.setattr(
        get_backend(),
        "delete_earnings_calendar_except",
        MagicMock(side_effect=RuntimeError("delete boom")),
    )

    failed = scheduler_handler.sync_earnings()

    assert failed == []
    fake_fetch.assert_called_once()


def test_run_notifies_for_todays_date_not_tomorrow(monkeypatch):
    """Regression: earnings dates from Finnhub are US-calendar dates. At the
    10:00 Asia/Taipei notify run (UTC 02:00), `date.today()` in UTC already
    equals the US earnings date, so `_run()` must query today's date, not
    tomorrow's — querying +1 day pushed notifications ~2 days too early."""
    fake_notify_local = MagicMock()
    monkeypatch.setattr(scheduler_handler, "_notify_local", fake_notify_local)

    scheduler_handler._run()

    fake_notify_local.assert_called_once_with(date.today().isoformat())


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
    assert rows == [("AAPL", TARGET_DATE, "*AAPL* 財報摘要")]


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


def test_handler_notify_mode_stale_banner_still_applies_after_split(
    local_mocks, local_store, monkeypatch
):
    """Regression: notify mode's earnings stage reuses `_run()`, so the
    existing `_is_stale`/`_stale_banner` behavior still fires post-split."""
    fake_push = MagicMock()
    monkeypatch.setattr(scheduler_handler, "push_daily_summary", fake_push)
    _seed_earnings(["AAPL"])
    with sqlite3.connect(local_store) as conn:
        conn.execute(
            "UPDATE earnings_calendar SET updated_at = '2020-01-01T00:00:00+00:00'"
        )

    scheduler_handler.handler({"mode": "notify"}, CTX)

    body = local_mocks["send"].call_args.args[1]
    assert "⚠️" in body
    assert "最後同步" in body
    fake_push.assert_called_once_with(refresh=False)


def test_local_skips_when_no_cached_earnings(local_mocks):
    scheduler_handler._run()

    local_mocks["agent"].assert_not_called()
    local_mocks["send"].assert_not_called()


@pytest.mark.parametrize(
    "fallback_attr",
    [
        "GROQ_ERROR_MESSAGE",
        "GROQ_RATE_LIMIT_MESSAGE",
        "GROQ_REQUEST_TOO_LARGE_MESSAGE",
        "TOOL_USE_FAILED_MESSAGE",
        "TOOL_ERROR_MESSAGE",
        "LOOP_EXCEEDED_MESSAGE",
    ],
)
def test_local_summary_groq_error_fallback_is_not_cached(
    local_mocks, local_store, fallback_attr
):
    """Regression: run_agent() returning a Groq/tool-loop failure fallback
    string (not raising) must not be cached as if it were a real summary —
    e.g. CRCL/2026-08-18 got GROQ_ERROR_MESSAGE cached for 30 days. Falls
    back to raw data and stays uncached so the next run retries."""
    from agent import agent as agent_module

    fallback_message = getattr(agent_module, fallback_attr)
    _seed_earnings(["AAPL"])
    local_mocks["agent"].return_value = fallback_message

    scheduler_handler._run()

    body = local_mocks["send"].call_args.args[1]
    assert "摘要生成失敗" in body
    assert "AAPL" in body
    assert fallback_message not in body
    # Fallback text is not cached, so a later run regenerates the summary.
    with sqlite3.connect(local_store) as conn:
        assert conn.execute("SELECT COUNT(*) FROM company_summaries").fetchone()[0] == 0


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
