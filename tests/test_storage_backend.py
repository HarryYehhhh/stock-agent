import sqlite3

import pytest

from tests.conftest import clear_db_caches


def test_defaults_to_local_outside_lambda(monkeypatch):
    from agent.db.backend import selected_backend_name

    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    clear_db_caches()

    assert selected_backend_name() == "local"


def test_defaults_to_dynamodb_in_lambda(monkeypatch):
    from agent.db.backend import selected_backend_name

    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "fn")
    clear_db_caches()

    assert selected_backend_name() == "dynamodb"


def test_storage_backend_override(monkeypatch):
    from agent.db.backend import selected_backend_name

    monkeypatch.setenv("STORAGE_BACKEND", "dynamodb")
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    clear_db_caches()

    assert selected_backend_name() == "dynamodb"


def test_invalid_storage_backend_fails(monkeypatch):
    from agent.db.backend import selected_backend_name

    monkeypatch.setenv("STORAGE_BACKEND", "bogus")
    clear_db_caches()

    with pytest.raises(ValueError, match="Unsupported STORAGE_BACKEND"):
        selected_backend_name()


def test_get_backend_returns_local_sqlite_backend_for_local(local_store):
    from agent.db.backend import LocalSQLiteBackend, get_backend

    assert isinstance(get_backend(), LocalSQLiteBackend)


def test_get_backend_returns_dynamodb_backend_for_dynamodb(monkeypatch):
    from agent.db.backend import DynamoDBBackend, get_backend

    monkeypatch.setenv("STORAGE_BACKEND", "dynamodb")

    assert isinstance(get_backend(), DynamoDBBackend)


def test_dynamodb_backend_claim_update_id_first_claim_wins(aws):
    from agent.db.backend import get_backend

    backend = get_backend()
    assert backend.claim_update_id(999) is True
    assert backend.claim_update_id(999) is False


def test_local_backend_upserts_earnings_calendar(local_store):
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

    with sqlite3.connect(local_store) as conn:
        rows = conn.execute(
            """
            SELECT symbol, earning_date, extended_hour, eps_estimate, revenue_estimate
            FROM earnings_calendar
            """
        ).fetchall()
        updated_at = conn.execute(
            "SELECT updated_at FROM earnings_calendar"
        ).fetchone()[0]

    assert rows == [("AAPL", "2026-07-23", "bmo", 1.5, 83000000000)]
    assert updated_at is not None


def test_schema_migration_of_pre_existing_store(monkeypatch, tmp_path):
    store_path = tmp_path / "old.sqlite3"
    with sqlite3.connect(store_path) as conn:
        conn.execute(
            """
            CREATE TABLE earnings_calendar (
                symbol TEXT NOT NULL,
                earning_date TEXT NOT NULL,
                extended_hour TEXT,
                eps_estimate REAL,
                revenue_estimate REAL,
                PRIMARY KEY (symbol, earning_date)
            )
            """
        )
        conn.execute(
            "INSERT INTO earnings_calendar VALUES ('AAPL', '2026-07-23', 'amc', 1.4, 82000000000)"
        )

    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORE_PATH", str(store_path))
    clear_db_caches()
    from agent.db.backend import get_backend

    get_backend()

    with sqlite3.connect(store_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(earnings_calendar)")}
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        row = conn.execute(
            "SELECT symbol, updated_at FROM earnings_calendar"
        ).fetchone()

    assert "updated_at" in columns
    assert "earnings_notifications" in tables
    assert "idx_earnings_date" in indexes
    assert row == ("AAPL", None)

    clear_db_caches()


def test_watchlist_earnings_join_and_notification_tracking(local_store):
    from agent.db.backend import get_backend

    backend = get_backend()
    backend.subscribe("111")
    backend.subscribe("222")
    backend.unsubscribe("222")  # inactive: excluded from the notification join
    backend.add_to_watchlist("111", "AAPL", exchange="NASDAQGS")
    backend.add_to_watchlist("111", "NVDA", exchange="NASDAQGS")
    backend.add_to_watchlist("222", "AAPL", exchange="NASDAQGS")
    backend.upsert_earnings_calendar(
        [
            {"symbol": "NVDA", "date": "2026-07-23", "hour": "amc"},
            {"symbol": "AAPL", "date": "2026-07-23", "hour": "bmo"},
            {"symbol": "ZZZZ", "date": "2026-07-23", "hour": "bmo"},
            {"symbol": "AAPL", "date": "2026-08-01", "hour": "amc"},
        ]
    )

    rows = backend.get_watchlist_earnings_for_date("2026-07-23")

    # Only active subscriber "111" gets rows; "222" is inactive and excluded.
    assert [(row["chat_id"], row["symbol"]) for row in rows] == [
        ("111", "AAPL"),
        ("111", "NVDA"),
    ]
    assert rows[0]["exchange"] == "NASDAQGS"
    assert rows[0]["updated_at"] is not None

    symbols = ["AAPL", "NVDA"]
    assert backend.unnotified_symbols("111", symbols, "2026-07-23") == symbols
    backend.mark_notified("111", symbols, "2026-07-23")
    assert backend.unnotified_symbols("111", symbols, "2026-07-23") == []
    assert backend.unnotified_symbols("222", symbols, "2026-07-23") == symbols
