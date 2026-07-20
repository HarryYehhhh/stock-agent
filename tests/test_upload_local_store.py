from agent.db.conversations import save_message
from agent.db.backend import LocalSQLiteBackend
from agent.db.dedup import claim_update_id
from agent.db.subscribers import subscribe


def _scan_counts(aws):
    return {
        "conversations": aws["ddb"].Table("test-conversations").scan()["Count"],
        "subscribers": aws["ddb"].Table("test-subscribers").scan()["Count"],
        "dedup": aws["ddb"].Table("test-processed").scan()["Count"],
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

    assert main([]) == 0

    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "conversations=1" in out
    assert "subscribers=1" in out
    assert "dedup=1" in out
    assert _scan_counts(aws) == {"conversations": 0, "subscribers": 0, "dedup": 0}


def test_upload_write_copies_rows_to_dynamodb(aws, local_store):
    from agent.db.upload_local_store import main

    save_message("c1", "user", "hello")
    save_message("c1", "assistant", "hi")
    subscribe("c1")
    claim_update_id(901)

    assert main(["--write"]) == 0

    conversations = aws["ddb"].Table("test-conversations").scan()["Items"]
    subscribers = aws["ddb"].Table("test-subscribers").scan()["Items"]
    dedup = aws["ddb"].Table("test-processed").scan()["Items"]

    assert [item["content"] for item in conversations] == ["hello", "hi"]
    assert subscribers == [{"chat_id": "c1", "subscribed_at": subscribers[0]["subscribed_at"], "active": True}]
    assert dedup == [{"update_id": 901, "ttl": dedup[0]["ttl"]}]


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
