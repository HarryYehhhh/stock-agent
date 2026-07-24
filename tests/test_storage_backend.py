import sqlite3

import pytest

from tests.conftest import NEWS, clear_db_caches


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

    # Only active subscriber "111" gets rows; "222" is inactive and excluded.
    assert [(row["chat_id"], row["symbol"]) for row in rows] == [
        ("111", "AAPL"),
        ("111", "NVDA"),
    ]
    assert rows[0]["updated_at"] is not None

    symbols = ["AAPL", "NVDA"]
    assert backend.unnotified_symbols("111", symbols, "2026-07-23") == symbols
    backend.mark_notified("111", symbols, "2026-07-23")
    assert backend.unnotified_symbols("111", symbols, "2026-07-23") == []
    assert backend.unnotified_symbols("222", symbols, "2026-07-23") == symbols


def test_summary_notified_idempotency(local_store):
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


def test_summary_notified_does_not_collide_with_earnings_notifications(local_store):
    from agent.db.backend import get_backend

    backend = get_backend()
    earning_date = "2026-07-23"

    # A real earnings notification must not make the summary sentinel look notified.
    backend.mark_notified("111", ["AAPL"], earning_date)
    assert backend.summary_notified("111", earning_date) is False

    # The summary sentinel must not make an unrelated real symbol look notified.
    backend.mark_summary_notified("111", earning_date)
    assert backend.unnotified_symbols("111", ["NVDA"], earning_date) == ["NVDA"]


def test_insert_news_returns_distinct_inserted_count_not_attempted_count(local_store):
    from agent.db.backend import get_backend

    backend = get_backend()
    items = [
        {
            "category": "general",
            "symbol": "AAPL",
            "headline": "Apple beats estimates",
            "summary": "...",
            "url": "https://example.com/1",
            "published_at": "2026-07-23T00:00:00Z",
        },
        {
            "category": "general",
            "symbol": "AAPL",
            "headline": "Apple beats estimates",  # duplicate headline within same batch
            "summary": "...",
            "url": "https://example.com/1-dup",
            "published_at": "2026-07-23T00:00:00Z",
        },
        {
            "category": "general",
            "symbol": "NVDA",
            "headline": "Nvidia ships new chip",
            "summary": "...",
            "url": "https://example.com/2",
            "published_at": "2026-07-23T00:00:00Z",
        },
    ]

    inserted = backend.insert_news(items)

    assert inserted == 2  # only 2 distinct headlines actually inserted

    with sqlite3.connect(local_store) as conn:
        total, distinct = conn.execute(
            "SELECT count(*), count(DISTINCT headline) FROM news"
        ).fetchone()

    assert total == distinct == 2


def test_insert_news_reinserting_existing_headlines_returns_zero(local_store):
    from agent.db.backend import get_backend

    backend = get_backend()
    items = [
        {
            "category": "general",
            "symbol": "AAPL",
            "headline": "Apple beats estimates",
            "summary": "...",
            "url": "https://example.com/1",
            "published_at": "2026-07-23T00:00:00Z",
        },
        {
            "category": "general",
            "symbol": "NVDA",
            "headline": "Nvidia ships new chip",
            "summary": "...",
            "url": "https://example.com/2",
            "published_at": "2026-07-23T00:00:00Z",
        },
    ]
    backend.insert_news(items)

    inserted_again = backend.insert_news(items)

    assert inserted_again == 0

    with sqlite3.connect(local_store) as conn:
        total, distinct = conn.execute(
            "SELECT count(*), count(DISTINCT headline) FROM news"
        ).fetchone()

    assert total == distinct == 2


# ---------------------------------------------------------------------------
# DynamoDBBackend: insert_news / list_news_since / insert_market_summary
# ---------------------------------------------------------------------------

_NEWS_ITEMS = [
    {
        "category": "general",
        "symbol": "AAPL",
        "headline": "Apple beats estimates",
        "summary": "...",
        "url": "https://example.com/1",
        "published_at": "2026-07-23T00:00:00Z",
    },
    {
        "category": "general",
        "symbol": "AAPL",
        "headline": "Apple beats estimates",  # duplicate headline within same batch
        "summary": "...",
        "url": "https://example.com/1-dup",
        "published_at": "2026-07-23T00:00:00Z",
    },
    {
        "category": "general",
        "symbol": "NVDA",
        "headline": "Nvidia ships new chip",
        "summary": "...",
        "url": "https://example.com/2",
        "published_at": "2026-07-23T00:00:00Z",
    },
]


def test_dynamodb_insert_news_returns_distinct_inserted_count_not_attempted_count(aws):
    from agent.db.backend import get_backend

    backend = get_backend()
    inserted = backend.insert_news(_NEWS_ITEMS)

    assert inserted == 2  # only 2 distinct headlines actually inserted

    table = aws["ddb"].Table(NEWS)
    items = table.scan()["Items"]
    headlines = [item["headline"] for item in items]
    assert len(items) == len(set(headlines)) == 2


def test_dynamodb_insert_news_reinserting_existing_headlines_returns_zero(aws):
    from agent.db.backend import get_backend

    backend = get_backend()
    backend.insert_news(_NEWS_ITEMS)

    inserted_again = backend.insert_news(_NEWS_ITEMS)

    assert inserted_again == 0
    table = aws["ddb"].Table(NEWS)
    assert len(table.scan()["Items"]) == 2


def test_dynamodb_list_news_since_filters_orders_and_shapes_rows(aws):
    from agent.db.backend import get_backend

    backend = get_backend()
    backend.insert_news(
        [
            {
                "category": "ai",
                "symbol": None,
                "headline": "Too old to include",
                "summary": "old",
                "url": None,
                "published_at": "2026-07-01T00:00:00Z",
            },
            {
                "category": "big7",
                "symbol": "AAPL",
                "headline": "Newer AAPL story",
                "summary": "s2",
                "url": "https://example.com/new",
                "published_at": "2026-07-23T12:00:00Z",
            },
            {
                "category": "big7",
                "symbol": "AAPL",
                "headline": "Older AAPL story",
                "summary": "s1",
                "url": "https://example.com/old",
                "published_at": "2026-07-23T00:00:00Z",
            },
        ]
    )

    rows = backend.list_news_since("2026-07-23T00:00:00Z")

    assert [row["headline"] for row in rows] == [
        "Newer AAPL story",
        "Older AAPL story",
    ]  # too-old row excluded; newest first
    row = rows[0]
    assert row["id"] == row["headline"] == "Newer AAPL story"
    assert row["category"] == "big7"
    assert row["symbol"] == "AAPL"
    assert row["summary"] == "s2"
    assert row["url"] == "https://example.com/new"
    assert row["published_at"] == "2026-07-23T12:00:00Z"


def test_dynamodb_insert_market_summary_is_a_noop(aws):
    from agent.db.backend import get_backend

    backend = get_backend()

    result = backend.insert_market_summary("some summary", ["headline-a", "headline-b"])

    assert result is None
    table = aws["ddb"].Table(NEWS)
    assert table.scan()["Items"] == []  # nothing persisted anywhere
