import sqlite3

from agent.db.backend import LocalSQLiteBackend

EXPECTED_WATCHLIST = [
    "AAOI",
    "AAPL",
    "AMD",
    "AMZN",
    "ARM",
    "ASML",
    "AXTI",
    "COHR",
    "CRCL",
    "CRWD",
    "DELL",
    "GLW",
    "GOOGL",
    "INTC",
    "LITE",
    "META",
    "MRVL",
    "MSFT",
    "MU",
    "NET",
    "NVDA",
    "QQQ",
    "SNDK",
    "SOFI",
    "SOXX",
    "SPCX",
    "TSLA",
    "TSM",
]


def test_local_store_creates_junction_watchlist_schema(local_store):
    LocalSQLiteBackend(local_store)

    with sqlite3.connect(local_store) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(watchlist)")}
        pk_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(watchlist)") if row[5] > 0
        }
        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }

    assert columns == {"chat_id", "symbol", "position"}
    assert pk_columns == {"chat_id", "symbol"}
    assert "idx_watchlist_symbol" in indexes


def test_local_store_does_not_seed_watchlist_on_construction(local_store):
    LocalSQLiteBackend(local_store)

    with sqlite3.connect(local_store) as conn:
        count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]

    assert count == 0


def test_migration_copies_old_global_watchlist_to_each_subscriber(monkeypatch, tmp_path):
    store_path = tmp_path / "old.sqlite3"
    with sqlite3.connect(store_path) as conn:
        conn.execute(
            """
            CREATE TABLE subscribers (
                chat_id TEXT PRIMARY KEY,
                subscribed_at TEXT NOT NULL,
                active INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO subscribers VALUES ('111', '2026-01-01T00:00:00', 1)"
        )
        conn.execute(
            "INSERT INTO subscribers VALUES ('222', '2026-01-01T00:00:00', 0)"
        )
        conn.execute(
            """
            CREATE TABLE watchlist (
                ticker TEXT PRIMARY KEY,
                exchange TEXT NOT NULL,
                position INTEGER NOT NULL
            )
            """
        )
        conn.execute("INSERT INTO watchlist VALUES ('AAPL', 'NASDAQGS', 0)")
        conn.execute("INSERT INTO watchlist VALUES ('NVDA', 'NASDAQGS', 1)")

    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("LOCAL_STORE_PATH", str(store_path))

    backend = LocalSQLiteBackend(store_path)

    assert backend.get_watchlist("111") == [
        {"symbol": "AAPL"},
        {"symbol": "NVDA"},
    ]
    assert backend.get_watchlist("222") == [
        {"symbol": "AAPL"},
        {"symbol": "NVDA"},
    ]

    with sqlite3.connect(store_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(watchlist)")}
    assert "chat_id" in columns

    # Idempotent: rebuild against already-migrated store doesn't duplicate/overwrite.
    backend.remove_from_watchlist("111", "NVDA")
    LocalSQLiteBackend(store_path)
    assert backend.get_watchlist("111") == [{"symbol": "AAPL"}]


def test_migration_discards_old_rows_when_no_subscribers(tmp_path):
    store_path = tmp_path / "old_no_subs.sqlite3"
    with sqlite3.connect(store_path) as conn:
        conn.execute(
            """
            CREATE TABLE subscribers (
                chat_id TEXT PRIMARY KEY,
                subscribed_at TEXT NOT NULL,
                active INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE watchlist (
                ticker TEXT PRIMARY KEY,
                exchange TEXT NOT NULL,
                position INTEGER NOT NULL
            )
            """
        )
        conn.execute("INSERT INTO watchlist VALUES ('AAPL', 'NASDAQGS', 0)")

    backend = LocalSQLiteBackend(store_path)

    with sqlite3.connect(store_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    assert count == 0
    assert backend.get_watchlist("anyone") == []


def test_migration_drops_exchange_column_from_existing_per_subscriber_db(tmp_path):
    store_path = tmp_path / "per_subscriber_with_exchange.sqlite3"
    with sqlite3.connect(store_path) as conn:
        conn.execute(
            """
            CREATE TABLE watchlist (
                chat_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT,
                position INTEGER NOT NULL,
                PRIMARY KEY (chat_id, symbol)
            )
            """
        )
        conn.execute("INSERT INTO watchlist VALUES ('111', 'AAPL', 'NASDAQGS', 0)")

    backend = LocalSQLiteBackend(store_path)

    with sqlite3.connect(store_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(watchlist)")}
    assert columns == {"chat_id", "symbol", "position"}
    assert backend.get_watchlist("111") == [{"symbol": "AAPL"}]


def test_get_watchlist_uses_code_defaults_for_dynamodb(monkeypatch):
    from agent.db.watchlist import get_watchlist

    monkeypatch.setenv("STORAGE_BACKEND", "dynamodb")

    assert [item["symbol"] for item in get_watchlist()] == EXPECTED_WATCHLIST


def test_add_remove_actually_work_for_dynamodb(aws):
    from agent.db.watchlist import add_to_watchlist, get_watchlist, remove_from_watchlist

    assert add_to_watchlist("111", "AAPL") is True
    assert get_watchlist("111") == [{"symbol": "AAPL"}]

    assert remove_from_watchlist("111", "AAPL") is True
    assert get_watchlist("111") == []


def test_seed_if_empty_writes_default_watchlist(local_store):
    from agent.db.watchlist import DEFAULT_WATCHLIST, get_watchlist, seed_watchlist_if_empty

    seed_watchlist_if_empty("111")

    assert [item["symbol"] for item in get_watchlist("111")] == [
        item["symbol"] for item in DEFAULT_WATCHLIST
    ]


def test_seed_if_empty_does_not_overwrite_existing_watchlist(local_store):
    from agent.db.watchlist import add_to_watchlist, get_watchlist, seed_watchlist_if_empty

    add_to_watchlist("111", "TSLA")
    seed_watchlist_if_empty("111")

    assert get_watchlist("111") == [{"symbol": "TSLA"}]


def test_custom_watchlist_survives_backend_reconstruction(local_store):
    from agent.db.backend import get_backend
    from agent.db.watchlist import add_to_watchlist, get_watchlist

    add_to_watchlist("111", "TSLA")

    # Reconstructing the backend must not wipe/reseed (old _seed_watchlist removed).
    LocalSQLiteBackend(local_store)
    get_backend.cache_clear()

    assert get_watchlist("111") == [{"symbol": "TSLA"}]


def test_add_to_watchlist_normalizes_uppercase_and_assigns_position(local_store):
    from agent.db.watchlist import add_to_watchlist, get_watchlist

    assert add_to_watchlist("111", "tsla") is True
    assert add_to_watchlist("111", "aapl") is True

    assert [item["symbol"] for item in get_watchlist("111")] == ["TSLA", "AAPL"]


def test_add_to_watchlist_duplicate_is_noop(local_store):
    from agent.db.watchlist import add_to_watchlist, get_watchlist

    assert add_to_watchlist("111", "TSLA") is True
    assert add_to_watchlist("111", "TSLA") is False

    assert [item["symbol"] for item in get_watchlist("111")] == ["TSLA"]


def test_remove_from_watchlist_reports_whether_removed(local_store):
    from agent.db.watchlist import add_to_watchlist, get_watchlist, remove_from_watchlist

    add_to_watchlist("111", "TSLA")

    assert remove_from_watchlist("111", "TSLA") is True
    assert remove_from_watchlist("111", "TSLA") is False
    assert get_watchlist("111") == []


def test_watchlists_are_isolated_per_chat_id(local_store):
    from agent.db.watchlist import add_to_watchlist, get_watchlist

    add_to_watchlist("111", "TSLA")
    add_to_watchlist("222", "AAPL")

    assert [item["symbol"] for item in get_watchlist("111")] == ["TSLA"]
    assert [item["symbol"] for item in get_watchlist("222")] == ["AAPL"]


def test_dynamodb_get_watchlist_scoped_to_chat_id_and_sorted_by_position(aws):
    from agent.db.watchlist import add_to_watchlist, get_watchlist

    add_to_watchlist("111", "tsla")
    add_to_watchlist("111", "aapl")
    add_to_watchlist("222", "NVDA")

    assert [item["symbol"] for item in get_watchlist("111")] == ["TSLA", "AAPL"]
    assert [item["symbol"] for item in get_watchlist("222")] == ["NVDA"]


def test_dynamodb_add_to_watchlist_duplicate_is_noop(aws):
    from agent.db.watchlist import add_to_watchlist, get_watchlist

    assert add_to_watchlist("111", "TSLA") is True
    assert add_to_watchlist("111", "tsla") is False

    assert [item["symbol"] for item in get_watchlist("111")] == ["TSLA"]


def test_dynamodb_remove_from_watchlist_reports_whether_removed(aws):
    from agent.db.watchlist import add_to_watchlist, get_watchlist, remove_from_watchlist

    add_to_watchlist("111", "TSLA")

    assert remove_from_watchlist("111", "tsla") is True
    assert remove_from_watchlist("111", "TSLA") is False
    assert get_watchlist("111") == []


def test_dynamodb_seed_watchlist_if_empty_seeds_default_only_when_empty(aws):
    from agent.db.watchlist import (
        DEFAULT_WATCHLIST,
        add_to_watchlist,
        get_watchlist,
        seed_watchlist_if_empty,
    )

    seed_watchlist_if_empty("111")
    assert [item["symbol"] for item in get_watchlist("111")] == [
        item["symbol"] for item in DEFAULT_WATCHLIST
    ]

    add_to_watchlist("222", "TSLA")
    seed_watchlist_if_empty("222")
    assert get_watchlist("222") == [{"symbol": "TSLA"}]
