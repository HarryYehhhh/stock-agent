from decimal import Decimal

from agent.db.backend import LocalSQLiteBackend, get_backend
from agent.db.conversations import save_message
from agent.db.dedup import claim_update_id
from agent.db.subscribers import subscribe
from agent.db.watchlist import add_to_watchlist


def _scan_counts(aws):
    return {
        "conversations": aws["ddb"].Table("test-conversations").scan()["Count"],
        "subscribers": aws["ddb"].Table("test-subscribers").scan()["Count"],
        "dedup": aws["ddb"].Table("test-processed").scan()["Count"],
        "watchlist": aws["ddb"].Table("test-watchlist").scan()["Count"],
        "earnings_calendar": aws["ddb"].Table("test-earnings-calendar").scan()["Count"],
        "company_summaries": aws["ddb"].Table("test-company-summaries").scan()["Count"],
        "news": aws["ddb"].Table("test-news").scan()["Count"],
    }


def test_upload_missing_local_store_fails(monkeypatch, tmp_path, capsys):
    from agent.db.upload_local_store import main

    missing = tmp_path / "missing.sqlite3"
    monkeypatch.setenv("LOCAL_STORE_PATH", str(missing))

    assert main([]) == 2
    assert "LOCAL_STORE_PATH does not exist" in capsys.readouterr().err


def test_upload_missing_local_store_path_env_fails(monkeypatch, capsys):
    from agent.db.upload_local_store import main

    monkeypatch.delenv("LOCAL_STORE_PATH", raising=False)

    assert main([]) == 2
    assert "LOCAL_STORE_PATH is required" in capsys.readouterr().err


def test_upload_invalid_storage_backend_fails(local_store, monkeypatch, capsys):
    from agent.db.upload_local_store import main

    save_message("c1", "user", "hello")
    monkeypatch.setenv("STORAGE_BACKEND", "bogus")

    assert main([]) == 2
    assert "Unsupported STORAGE_BACKEND" in capsys.readouterr().err


def test_upload_dry_run_default_does_not_write(aws, local_store, capsys):
    from agent.db.upload_local_store import main

    save_message("c1", "user", "hello")
    subscribe("c1")
    claim_update_id(900)
    add_to_watchlist("c1", "AAPL")
    get_backend().upsert_earnings_calendar(
        [{"symbol": "AAPL", "date": "2026-07-23", "epsEstimate": 1.42}]
    )
    get_backend().upsert_company_summary("AAPL", "2026-07-23", "summary text")
    get_backend().insert_news(
        [
            {
                "category": "big7",
                "symbol": "AAPL",
                "headline": "Apple beats estimates",
                "summary": "...",
                "url": "https://example.com/1",
                "published_at": "2026-07-23T00:00:00+00:00",
            }
        ]
    )

    assert main([]) == 0

    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "conversations=1" in out
    assert "subscribers=1" in out
    assert "dedup=1" in out
    assert "watchlist=1" in out
    assert "earnings_calendar=1" in out
    assert "company_summaries=1" in out
    assert "news=1" in out
    assert _scan_counts(aws) == {
        "conversations": 0,
        "subscribers": 0,
        "dedup": 0,
        "watchlist": 0,
        "earnings_calendar": 0,
        "company_summaries": 0,
        "news": 0,
    }


def test_upload_write_copies_rows_to_dynamodb(aws, local_store):
    from agent.db.upload_local_store import main

    save_message("c1", "user", "hello")
    save_message("c1", "assistant", "hi")
    subscribe("c1")
    claim_update_id(901)
    add_to_watchlist("c1", "AAPL")
    add_to_watchlist("c1", "NVDA")
    get_backend().upsert_earnings_calendar(
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
    get_backend().upsert_company_summary("AAPL", "2026-07-23", "summary text")
    get_backend().insert_news(
        [
            {
                "category": "big7",
                "symbol": "AAPL",
                "headline": "Apple beats estimates",
                "summary": "solid quarter",
                "url": "https://example.com/1",
                "published_at": "2026-07-23T00:00:00+00:00",
            },
            {
                "category": "ai",
                "symbol": None,
                "headline": "New model released",
                "summary": "",
                "url": None,
                "published_at": "2026-07-22T00:00:00+00:00",
            },
        ]
    )

    assert main(["--write"]) == 0

    conversations = aws["ddb"].Table("test-conversations").scan()["Items"]
    subscribers = aws["ddb"].Table("test-subscribers").scan()["Items"]
    dedup = aws["ddb"].Table("test-processed").scan()["Items"]
    watchlist = aws["ddb"].Table("test-watchlist").scan()["Items"]
    earnings_calendar = aws["ddb"].Table("test-earnings-calendar").scan()["Items"]
    company_summaries = aws["ddb"].Table("test-company-summaries").scan()["Items"]
    news = aws["ddb"].Table("test-news").scan()["Items"]

    assert [item["content"] for item in conversations] == ["hello", "hi"]
    assert subscribers == [{"chat_id": "c1", "subscribed_at": subscribers[0]["subscribed_at"], "active": True}]
    assert dedup == [{"update_id": 901, "ttl": dedup[0]["ttl"]}]
    assert sorted(item["symbol"] for item in watchlist) == ["AAPL", "NVDA"]

    assert len(earnings_calendar) == 1
    ec = earnings_calendar[0]
    assert ec["symbol"] == "AAPL"
    assert ec["earning_date"] == "2026-07-23"
    assert ec["extended_hour"] == "amc"
    assert ec["eps_estimate"] == Decimal("1.42")
    assert ec["revenue_estimate"] == Decimal("82000000000")
    assert ec["ttl"] > 0

    assert len(company_summaries) == 1
    cs = company_summaries[0]
    assert cs["symbol"] == "AAPL"
    assert cs["earning_date"] == "2026-07-23"
    assert cs["summary"] == "summary text"
    assert cs["ttl"] > 0

    assert len(news) == 2
    news_by_headline = {item["headline"]: item for item in news}
    big7 = news_by_headline["Apple beats estimates"]
    assert big7["symbol"] == "AAPL"
    assert big7["category"] == "big7"
    assert big7["bucket"] == "news"
    assert big7["url"] == "https://example.com/1"
    assert big7["ttl"] > 0
    ai = news_by_headline["New model released"]
    assert "symbol" not in ai
    assert "url" not in ai
    assert ai["category"] == "ai"

    # earnings_notifications remains cache-only and stays out of upload scope.
    assert aws["ddb"].Table("test-earnings-notifications").scan()["Count"] == 0


def test_upload_write_is_idempotent(aws, local_store):
    """Rerunning the upload with the same local data must not duplicate rows."""
    from agent.db.upload_local_store import main

    get_backend().upsert_earnings_calendar([{"symbol": "AAPL", "date": "2026-07-23"}])
    get_backend().upsert_company_summary("AAPL", "2026-07-23", "summary v1")
    get_backend().insert_news(
        [
            {
                "category": "big7",
                "symbol": "AAPL",
                "headline": "Apple beats estimates",
                "summary": "v1",
                "url": None,
                "published_at": "2026-07-23T00:00:00+00:00",
            }
        ]
    )

    assert main(["--write"]) == 0
    assert main(["--write"]) == 0

    assert aws["ddb"].Table("test-earnings-calendar").scan()["Count"] == 1
    assert aws["ddb"].Table("test-company-summaries").scan()["Count"] == 1
    assert aws["ddb"].Table("test-news").scan()["Count"] == 1


def test_upload_write_failure_returns_nonzero_and_preserves_local_store(local_store, monkeypatch, capsys):
    from agent.db import upload_local_store

    save_message("c1", "user", "hello")
    subscribe("c1")
    claim_update_id(902)
    before = LocalSQLiteBackend(local_store).export_rows()

    class FailingTable:
        def put_item(self, Item):
            raise RuntimeError("put failed")

    class FailingResource:
        def Table(self, name):
            return FailingTable()

    monkeypatch.setattr(upload_local_store.boto3, "resource", lambda *_, **__: FailingResource())

    assert upload_local_store.main(["--write"]) == 1
    assert "DynamoDB upload failed" in capsys.readouterr().err
    assert LocalSQLiteBackend(local_store).export_rows() == before
