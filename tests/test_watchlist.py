import sqlite3

from agent.db.backend import LocalSQLiteBackend

EXPECTED_WATCHLIST = [
    ("NASDAQGM", "AAOI"),
    ("NASDAQGS", "AAPL"),
    ("NASDAQGS", "AMD"),
    ("NASDAQGS", "AMZN"),
    ("NASDAQGS", "ARM"),
    ("NASDAQGS", "ASML"),
    ("NASDAQGS", "AXTI"),
    ("NYSE", "COHR"),
    ("NYSE", "CRCL"),
    ("NASDAQGS", "CRWD"),
    ("NYSE", "DELL"),
    ("NYSE", "GLW"),
    ("NASDAQGS", "GOOGL"),
    ("NASDAQGS", "INTC"),
    ("NASDAQGS", "LITE"),
    ("NASDAQGS", "META"),
    ("NASDAQGS", "MRVL"),
    ("NASDAQGS", "MSFT"),
    ("NASDAQGS", "MU"),
    ("NYSE", "NET"),
    ("NASDAQGS", "NVDA"),
    ("NASDAQGM", "QQQ"),
    ("NASDAQGS", "SNDK"),
    ("NASDAQGS", "SOFI"),
    ("NASDAQGM", "SOXX"),
    ("NASDAQGS", "SPCX"),
    ("NASDAQGS", "TSLA"),
    ("NYSE", "TSM"),
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

    assert columns == {"chat_id", "symbol", "exchange", "position"}
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
        {"symbol": "AAPL", "exchange": "NASDAQGS"},
        {"symbol": "NVDA", "exchange": "NASDAQGS"},
    ]
    assert backend.get_watchlist("222") == [
        {"symbol": "AAPL", "exchange": "NASDAQGS"},
        {"symbol": "NVDA", "exchange": "NASDAQGS"},
    ]

    with sqlite3.connect(store_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(watchlist)")}
    assert "chat_id" in columns

    # Idempotent: rebuild against already-migrated store doesn't duplicate/overwrite.
    backend.remove_from_watchlist("111", "NVDA")
    LocalSQLiteBackend(store_path)
    assert backend.get_watchlist("111") == [
        {"symbol": "AAPL", "exchange": "NASDAQGS"}
    ]


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


def test_get_watchlist_uses_code_defaults_for_dynamodb(monkeypatch):
    from agent.db.watchlist import get_watchlist

    monkeypatch.setenv("STORAGE_BACKEND", "dynamodb")

    assert [
        (item["exchange"], item["symbol"]) for item in get_watchlist()
    ] == EXPECTED_WATCHLIST


def test_add_remove_raise_not_implemented_for_dynamodb(monkeypatch):
    import pytest

    from agent.db.watchlist import add_to_watchlist, remove_from_watchlist

    monkeypatch.setenv("STORAGE_BACKEND", "dynamodb")

    with pytest.raises(NotImplementedError):
        add_to_watchlist("111", "AAPL")
    with pytest.raises(NotImplementedError):
        remove_from_watchlist("111", "AAPL")


def test_seed_if_empty_writes_default_watchlist(local_store):
    from agent.db.watchlist import DEFAULT_WATCHLIST, get_watchlist, seed_watchlist_if_empty

    seed_watchlist_if_empty("111")

    assert [
        (item["exchange"], item["symbol"]) for item in get_watchlist("111")
    ] == [(item["exchange"], item["symbol"]) for item in DEFAULT_WATCHLIST]


def test_seed_if_empty_does_not_overwrite_existing_watchlist(local_store):
    from agent.db.watchlist import add_to_watchlist, get_watchlist, seed_watchlist_if_empty

    add_to_watchlist("111", "TSLA")
    seed_watchlist_if_empty("111")

    assert get_watchlist("111") == [{"symbol": "TSLA", "exchange": None}]


def test_custom_watchlist_survives_backend_reconstruction(local_store):
    from agent.db.backend import get_backend
    from agent.db.watchlist import add_to_watchlist, get_watchlist

    add_to_watchlist("111", "TSLA")

    # Reconstructing the backend must not wipe/reseed (old _seed_watchlist removed).
    LocalSQLiteBackend(local_store)
    get_backend.cache_clear()

    assert get_watchlist("111") == [{"symbol": "TSLA", "exchange": None}]


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
