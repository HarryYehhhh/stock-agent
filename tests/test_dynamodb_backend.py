"""DynamoDBBackend method coverage for the four new production tables
(watchlist / earnings_calendar / earnings_notifications / company_summaries),
mirroring the LocalSQLiteBackend behaviour parity tests in test_storage_backend.py.
"""

from __future__ import annotations


def test_dynamodb_earnings_cache_and_active_symbols_join(aws):
    from agent.db.backend import get_backend

    backend = get_backend()
    backend.subscribe("111")
    backend.subscribe("222")
    backend.add_to_watchlist("111", "AAPL")
    backend.add_to_watchlist("111", "NVDA")
    backend.add_to_watchlist("222", "AAPL")
    backend.upsert_earnings_calendar(
        [
            {
                "symbol": "aapl",
                "date": "2026-07-23",
                "hour": "bmo",
                "epsEstimate": 1.42,
                "revenueEstimate": 82000000000,
            },
            {"symbol": "NVDA", "date": "2026-07-23", "hour": "amc"},
        ]
    )

    symbols = backend.get_active_watchlist_symbols()
    assert symbols == [{"symbol": "AAPL"}, {"symbol": "NVDA"}]


def test_dynamodb_upsert_earnings_calendar_normalizes_and_converts_types(aws):
    from agent.db.backend import get_backend

    backend = get_backend()
    backend.upsert_earnings_calendar(
        [
            {
                "symbol": "aapl",
                "date": "2026-07-23",
                "hour": "amc",
                "epsEstimate": 1.42,
                "revenueEstimate": 82000000000,
            }
        ]
    )
    backend.upsert_earnings_calendar(
        [
            {
                "symbol": "AAPL",
                "date": "2026-07-23",
                "hour": "bmo",
                "epsEstimate": 1.5,
                "revenueEstimate": 83000000000,
            }
        ]
    )
    # Missing symbol/date is skipped rather than erroring.
    backend.upsert_earnings_calendar([{"symbol": "NVDA"}])

    rows = backend.get_chat_watchlist_earnings("111", "2026-01-01", "2026-12-31")
    assert rows == []  # "111" has no watchlist symbols yet

    backend.add_to_watchlist("111", "AAPL")
    rows = backend.get_chat_watchlist_earnings("111", "2026-01-01", "2026-12-31")
    assert rows == [
        {
            "symbol": "AAPL",
            "earning_date": "2026-07-23",
            "extended_hour": "bmo",
            "eps_estimate": 1.5,
            "revenue_estimate": 83000000000.0,
            "updated_at": rows[0]["updated_at"],
        }
    ]
    assert isinstance(rows[0]["eps_estimate"], float)
    assert isinstance(rows[0]["revenue_estimate"], float)


def test_dynamodb_earnings_calendar_omits_none_estimates(aws):
    from agent.db.backend import get_backend

    backend = get_backend()
    backend.add_to_watchlist("111", "AAPL")
    backend.upsert_earnings_calendar(
        [{"symbol": "AAPL", "date": "2026-07-23", "hour": "amc"}]
    )

    rows = backend.get_chat_watchlist_earnings("111", "2026-01-01", "2026-12-31")

    assert rows[0]["eps_estimate"] is None
    assert rows[0]["revenue_estimate"] is None


def test_dynamodb_delete_earnings_calendar_except_removes_stale_rows(aws):
    """Regression: Finnhub moving a company's earnings date (e.g. CRCL cached
    for 8/18, later rescheduled to 8/5) must not leave the old date's row
    behind until its 45-day TTL expires — that stale row triggers a false
    earnings-reminder push."""
    from agent.db.backend import get_backend

    backend = get_backend()
    backend.add_to_watchlist("111", "CRCL")
    # Old cached row (from an earlier sync, before Finnhub moved the date).
    backend.upsert_earnings_calendar([{"symbol": "CRCL", "date": "2026-08-18", "hour": "amc"}])
    # Latest Finnhub fetch: the earnings date moved to 8/5.
    backend.upsert_earnings_calendar([{"symbol": "CRCL", "date": "2026-08-05", "hour": "amc"}])

    backend.delete_earnings_calendar_except(
        "CRCL", keep_dates=["2026-08-05"], date_from="2026-08-05"
    )

    rows = backend.get_chat_watchlist_earnings("111", "2026-01-01", "2026-12-31")
    assert [row["earning_date"] for row in rows] == ["2026-08-05"]


def test_dynamodb_delete_earnings_calendar_except_keeps_past_and_other_symbol_rows(aws):
    from agent.db.backend import get_backend

    backend = get_backend()
    backend.add_to_watchlist("111", "CRCL")
    backend.add_to_watchlist("111", "AAPL")
    backend.upsert_earnings_calendar(
        [
            {"symbol": "CRCL", "date": "2026-08-01", "hour": "amc"},  # past, untouched
            {"symbol": "CRCL", "date": "2026-08-05", "hour": "amc"},  # current, kept
            {"symbol": "AAPL", "date": "2026-08-18", "hour": "amc"},  # other symbol, untouched
        ]
    )

    backend.delete_earnings_calendar_except(
        "CRCL", keep_dates=["2026-08-05"], date_from="2026-08-18"
    )

    rows = backend.get_chat_watchlist_earnings("111", "2026-01-01", "2026-12-31")
    got = {(row["symbol"], row["earning_date"]) for row in rows}
    assert got == {("CRCL", "2026-08-01"), ("CRCL", "2026-08-05"), ("AAPL", "2026-08-18")}


def test_dynamodb_watchlist_earnings_date_range_boundaries(aws):
    """earning_date outside [date_from, date_to] must be excluded; boundaries are inclusive."""
    from agent.db.backend import get_backend

    backend = get_backend()
    backend.add_to_watchlist("111", "AAPL")
    backend.upsert_earnings_calendar(
        [
            {"symbol": "AAPL", "date": "2026-06-30", "hour": "amc"},  # before range
            {"symbol": "AAPL", "date": "2026-07-01", "hour": "bmo"},  # == date_from
            {"symbol": "AAPL", "date": "2026-07-15", "hour": "amc"},  # inside range
            {"symbol": "AAPL", "date": "2026-07-31", "hour": "bmo"},  # == date_to
            {"symbol": "AAPL", "date": "2026-08-01", "hour": "amc"},  # after range
        ]
    )

    rows = backend.get_chat_watchlist_earnings("111", "2026-07-01", "2026-07-31")

    assert [row["earning_date"] for row in rows] == [
        "2026-07-01",
        "2026-07-15",
        "2026-07-31",
    ]


def test_dynamodb_watchlist_earnings_for_date_scopes_rows_per_subscriber(aws):
    from agent.db.backend import get_backend

    backend = get_backend()
    backend.subscribe("111")
    backend.subscribe("222")
    backend.unsubscribe("222")  # inactive: excluded from the notification join
    backend.add_to_watchlist("111", "AAPL")
    backend.add_to_watchlist("111", "NVDA")
    backend.add_to_watchlist("222", "AAPL")
    backend.upsert_earnings_calendar(
        [
            {"symbol": "NVDA", "date": "2026-07-23", "hour": "amc"},
            {"symbol": "AAPL", "date": "2026-07-23", "hour": "bmo"},
            {"symbol": "ZZZZ", "date": "2026-07-23", "hour": "bmo"},
            {"symbol": "AAPL", "date": "2026-08-01", "hour": "amc"},
        ]
    )

    rows = backend.get_watchlist_earnings_for_date("2026-07-23")

    assert [(row["chat_id"], row["symbol"]) for row in rows] == [
        ("111", "AAPL"),
        ("111", "NVDA"),
    ]
    assert rows[0]["updated_at"] is not None


def test_dynamodb_watchlist_earnings_for_date_two_subscribers_get_only_own_symbols(aws):
    from agent.db.backend import get_backend

    backend = get_backend()
    backend.subscribe("111")
    backend.subscribe("222")
    backend.add_to_watchlist("111", "AAPL")
    backend.add_to_watchlist("222", "NVDA")
    backend.upsert_earnings_calendar(
        [
            {"symbol": "AAPL", "date": "2026-07-23", "hour": "bmo"},
            {"symbol": "NVDA", "date": "2026-07-23", "hour": "amc"},
        ]
    )

    rows = backend.get_watchlist_earnings_for_date("2026-07-23")
    by_chat_id = {row["chat_id"]: row["symbol"] for row in rows}

    assert by_chat_id == {"111": "AAPL", "222": "NVDA"}


def test_dynamodb_notification_idempotency(aws):
    from agent.db.backend import get_backend

    backend = get_backend()
    symbols = ["AAPL", "NVDA"]

    assert backend.unnotified_symbols("111", symbols, "2026-07-23") == symbols
    backend.mark_notified("111", symbols, "2026-07-23")
    assert backend.unnotified_symbols("111", symbols, "2026-07-23") == []
    # Different chat_id / earning_date are unaffected.
    assert backend.unnotified_symbols("222", symbols, "2026-07-23") == symbols

    # Re-running mark_notified for an already-notified symbol must not raise.
    backend.mark_notified("111", symbols, "2026-07-23")
    assert backend.unnotified_symbols("111", symbols, "2026-07-23") == []


def test_dynamodb_unnotified_symbols_empty_input_returns_empty(aws):
    from agent.db.backend import get_backend

    backend = get_backend()
    assert backend.unnotified_symbols("111", [], "2026-07-23") == []


def test_dynamodb_summary_notified_idempotency(aws):
    from agent.db.backend import get_backend

    backend = get_backend()

    assert backend.summary_notified("111", "2026-07-23") is False
    backend.mark_summary_notified("111", "2026-07-23")
    assert backend.summary_notified("111", "2026-07-23") is True

    # Different chat_id / date are unaffected.
    assert backend.summary_notified("222", "2026-07-23") is False
    assert backend.summary_notified("111", "2026-07-24") is False

    # Re-marking must not raise (retry-safe).
    backend.mark_summary_notified("111", "2026-07-23")
    assert backend.summary_notified("111", "2026-07-23") is True


def test_dynamodb_summary_notified_does_not_collide_with_earnings_notifications(aws):
    from agent.db.backend import get_backend

    backend = get_backend()
    earning_date = "2026-07-23"

    # A real earnings notification must not make the summary sentinel look notified.
    backend.mark_notified("111", ["AAPL"], earning_date)
    assert backend.summary_notified("111", earning_date) is False

    # The summary sentinel must not make an unrelated real symbol look notified.
    backend.mark_summary_notified("111", earning_date)
    assert backend.unnotified_symbols("111", ["NVDA"], earning_date) == ["NVDA"]


def test_dynamodb_company_summary_upsert_and_get(aws):
    from agent.db.backend import get_backend

    backend = get_backend()
    assert backend.get_company_summary("AAPL", "2026-07-23") is None

    backend.upsert_company_summary("AAPL", "2026-07-23", "summary v1")
    assert backend.get_company_summary("AAPL", "2026-07-23") == "summary v1"

    backend.upsert_company_summary("AAPL", "2026-07-23", "summary v2")
    assert backend.get_company_summary("AAPL", "2026-07-23") == "summary v2"


def test_dynamodb_local_parity_for_watchlist_and_earnings_join(aws, local_store):
    """Same inputs on both backends must yield equivalent public results.

    Backends are instantiated directly (bypassing get_backend()/STORAGE_BACKEND)
    since both the `aws` and `local_store` fixtures are active simultaneously.
    """
    from agent.db.backend import DynamoDBBackend, LocalSQLiteBackend

    def _drive(backend) -> dict:
        backend.subscribe("111")
        backend.subscribe("222")
        backend.add_to_watchlist("111", "aapl")
        backend.add_to_watchlist("111", "nvda")
        backend.add_to_watchlist("222", "aapl")
        backend.upsert_earnings_calendar(
            [
                {"symbol": "AAPL", "date": "2026-07-23", "hour": "bmo"},
                {"symbol": "NVDA", "date": "2026-07-23", "hour": "amc"},
            ]
        )
        return {
            "active_symbols": [item["symbol"] for item in backend.get_active_watchlist_symbols()],
            "notify_rows": sorted(
                (row["chat_id"], row["symbol"])
                for row in backend.get_watchlist_earnings_for_date("2026-07-23")
            ),
        }

    local_result = _drive(LocalSQLiteBackend(local_store))
    dynamo_result = _drive(DynamoDBBackend())

    assert local_result == dynamo_result
