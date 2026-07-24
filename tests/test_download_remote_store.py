from agent.db.backend import LocalSQLiteBackend, get_backend
from agent.db.conversations import save_message
from agent.db.subscribers import subscribe
from agent.db.watchlist import add_to_watchlist


def test_scan_all_paginates():
    """DynamoDB pagination via LastEvaluatedKey must be followed to completion."""
    from agent.db.download_remote_store import _scan_all

    class FakeTable:
        def __init__(self):
            self.calls: list[dict] = []

        def scan(self, **kwargs):
            self.calls.append(kwargs)
            if "ExclusiveStartKey" not in kwargs:
                return {
                    "Items": [{"chat_id": "c1", "symbol": "AAA"}],
                    "LastEvaluatedKey": {"chat_id": "c1", "symbol": "AAA"},
                }
            return {"Items": [{"chat_id": "c1", "symbol": "BBB"}]}

    table = FakeTable()
    items = _scan_all(table)

    assert items == [
        {"chat_id": "c1", "symbol": "AAA"},
        {"chat_id": "c1", "symbol": "BBB"},
    ]
    assert len(table.calls) == 2
    assert table.calls[1]["ExclusiveStartKey"] == {"chat_id": "c1", "symbol": "AAA"}


def test_downsync_full_mirror_removes_local_symbol_not_in_dynamodb(aws, local_store):
    """Cloud deletion propagates: local has A/B/C, DynamoDB only has A/C -> B is dropped."""
    from agent.db import download_remote_store

    add_to_watchlist("c1", "A")
    add_to_watchlist("c1", "B")
    add_to_watchlist("c1", "C")

    aws["ddb"].Table("test-watchlist").put_item(
        Item={"chat_id": "c1", "symbol": "A", "position": 0}
    )
    aws["ddb"].Table("test-watchlist").put_item(
        Item={"chat_id": "c1", "symbol": "C", "position": 1}
    )

    assert download_remote_store.main(["--write"]) == 0

    rows = LocalSQLiteBackend(local_store).export_rows()
    assert sorted(row["symbol"] for row in rows["watchlist"]) == ["A", "C"]


def test_downsync_subscribers_active_type_conversion(aws, local_store):
    """DynamoDB Bool active -> local INTEGER 1/0; get_all_active() only returns active=1."""
    from agent.db import download_remote_store

    aws["ddb"].Table("test-subscribers").put_item(
        Item={"chat_id": "c1", "subscribed_at": "2026-01-01T00:00:00+00:00", "active": True}
    )
    aws["ddb"].Table("test-subscribers").put_item(
        Item={"chat_id": "c2", "subscribed_at": "2026-01-01T00:00:00+00:00", "active": False}
    )

    assert download_remote_store.main(["--write"]) == 0

    with LocalSQLiteBackend(local_store)._connect() as conn:
        active_by_chat_id = {
            row["chat_id"]: row["active"]
            for row in conn.execute("SELECT chat_id, active FROM subscribers")
        }
    assert active_by_chat_id == {"c1": 1, "c2": 0}
    assert get_backend().get_all_active() == ["c1"]


def test_downsync_subscriber_missing_subscribed_at_backfills_now(aws, local_store):
    """subscribed_at is NOT NULL locally; a missing value is backfilled with now()."""
    from agent.db import download_remote_store

    aws["ddb"].Table("test-subscribers").put_item(Item={"chat_id": "c1", "active": True})

    assert download_remote_store.main(["--write"]) == 0

    with LocalSQLiteBackend(local_store)._connect() as conn:
        row = conn.execute(
            "SELECT subscribed_at FROM subscribers WHERE chat_id = 'c1'"
        ).fetchone()
    assert row["subscribed_at"]


def test_downsync_insert_failure_rolls_back_and_preserves_local_store(
    aws, local_store, monkeypatch, capsys
):
    """A mid-INSERT failure must roll back both tables to their pre-overwrite state."""
    from agent.db import download_remote_store

    add_to_watchlist("old_chat", "OLDSYM")
    subscribe("old_chat")
    before = LocalSQLiteBackend(local_store).export_rows()

    aws["ddb"].Table("test-watchlist").put_item(
        Item={"chat_id": "c1", "symbol": "AAPL", "position": 0}
    )
    aws["ddb"].Table("test-subscribers").put_item(
        Item={"chat_id": "c1", "subscribed_at": "2026-01-01T00:00:00+00:00", "active": True}
    )

    def _boom(item):
        raise RuntimeError("bad row")

    monkeypatch.setattr(download_remote_store, "_subscriber_row", _boom)

    assert download_remote_store.main(["--write"]) == 1
    assert "failed" in capsys.readouterr().err.lower()

    after = LocalSQLiteBackend(local_store).export_rows()
    assert after["watchlist"] == before["watchlist"]
    assert after["subscribers"] == before["subscribers"]


def test_downsync_scan_failure_aborts_before_local_write(
    aws, local_store, monkeypatch, capsys
):
    """A DynamoDB scan error must abort before any local write."""
    from agent.db import download_remote_store

    add_to_watchlist("c1", "AAPL")
    before = LocalSQLiteBackend(local_store).export_rows()

    class FailingTable:
        def scan(self, **kwargs):
            raise RuntimeError("scan failed")

    class FailingResource:
        def Table(self, name):
            return FailingTable()

    monkeypatch.setattr(
        download_remote_store.boto3, "resource", lambda *_, **__: FailingResource()
    )

    assert download_remote_store.main(["--write"]) == 1
    assert "scan" in capsys.readouterr().err.lower()

    after = LocalSQLiteBackend(local_store).export_rows()
    assert after == before


def test_downsync_dry_run_default_does_not_write(aws, local_store, capsys):
    from agent.db import download_remote_store

    add_to_watchlist("c1", "AAPL")
    before = LocalSQLiteBackend(local_store).export_rows()

    aws["ddb"].Table("test-watchlist").put_item(
        Item={"chat_id": "c2", "symbol": "NVDA", "position": 0}
    )
    aws["ddb"].Table("test-subscribers").put_item(
        Item={"chat_id": "c2", "subscribed_at": "2026-01-01T00:00:00+00:00", "active": True}
    )

    assert download_remote_store.main([]) == 0

    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "watchlist=1" in out
    assert "subscribers=1" in out

    after = LocalSQLiteBackend(local_store).export_rows()
    assert after == before


def test_downsync_write_is_idempotent(aws, local_store):
    from agent.db import download_remote_store

    aws["ddb"].Table("test-watchlist").put_item(
        Item={"chat_id": "c1", "symbol": "AAPL", "position": 0}
    )
    aws["ddb"].Table("test-subscribers").put_item(
        Item={"chat_id": "c1", "subscribed_at": "2026-01-01T00:00:00+00:00", "active": True}
    )

    assert download_remote_store.main(["--write"]) == 0
    first = LocalSQLiteBackend(local_store).export_rows()
    assert download_remote_store.main(["--write"]) == 0
    second = LocalSQLiteBackend(local_store).export_rows()

    assert first["watchlist"] == second["watchlist"]
    assert first["subscribers"] == second["subscribers"]


def test_downsync_does_not_touch_other_local_tables(aws, local_store):
    """Downsync must only ever touch watchlist + subscribers locally."""
    from agent.db import download_remote_store

    save_message("c1", "user", "hello")
    get_backend().upsert_earnings_calendar([{"symbol": "AAPL", "date": "2026-07-23"}])
    get_backend().insert_news(
        [
            {
                "category": "big7",
                "symbol": "AAPL",
                "headline": "Apple beats estimates",
                "summary": "s",
                "url": None,
                "published_at": "2026-07-23T00:00:00+00:00",
            }
        ]
    )
    before = LocalSQLiteBackend(local_store).export_rows()

    aws["ddb"].Table("test-watchlist").put_item(
        Item={"chat_id": "c1", "symbol": "AAPL", "position": 0}
    )

    assert download_remote_store.main(["--write"]) == 0

    after = LocalSQLiteBackend(local_store).export_rows()
    assert after["conversations"] == before["conversations"]
    assert after["earnings_calendar"] == before["earnings_calendar"]
    assert after["news"] == before["news"]
