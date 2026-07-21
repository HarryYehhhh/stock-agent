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


def test_local_store_seeds_fixed_watchlist(local_store):
    LocalSQLiteBackend(local_store)

    with sqlite3.connect(local_store) as conn:
        rows = conn.execute(
            "SELECT exchange, ticker FROM watchlist ORDER BY position"
        ).fetchall()

    assert rows == EXPECTED_WATCHLIST


def test_get_watchlist_reads_local_sqlite(local_store):
    from agent.db.watchlist import get_watchlist

    assert get_watchlist()[0] == {"exchange": "NASDAQGM", "ticker": "AAOI"}
    assert get_watchlist()[-1] == {"exchange": "NYSE", "ticker": "TSM"}


def test_get_watchlist_uses_code_defaults_for_dynamodb(monkeypatch):
    from agent.db.watchlist import get_watchlist

    monkeypatch.setenv("STORAGE_BACKEND", "dynamodb")

    assert [
        (item["exchange"], item["ticker"]) for item in get_watchlist()
    ] == EXPECTED_WATCHLIST
