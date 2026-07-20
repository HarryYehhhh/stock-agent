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

    assert rows == [("AAPL", "2026-07-23", "bmo", 1.5, 83000000000)]
