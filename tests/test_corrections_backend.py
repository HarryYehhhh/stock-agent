"""StorageBackend corrections persistence (ADR-0014): save_correction /
list_corrections / update_correction_status round-trip parity between
LocalSQLiteBackend and DynamoDBBackend, and the pending->approved/rejected
state machine.
"""

from __future__ import annotations

from agent.db.backend import get_backend


def _assert_save_and_show(backend) -> None:
    correction_id = backend.save_correction(
        "111", "AAPL 財報應該是 8/1 盤後", "AAPL 財報什麼時候？", "AAPL 財報是 7/31 盤前"
    )
    assert correction_id

    results = backend.list_corrections()
    assert len(results) == 1
    got = results[0]
    assert got["correction_id"] == correction_id
    assert got["chat_id"] == "111"
    assert got["correction_text"] == "AAPL 財報應該是 8/1 盤後"
    assert got["context_question"] == "AAPL 財報什麼時候？"
    assert got["context_answer"] == "AAPL 財報是 7/31 盤前"
    assert got["status"] == "pending"


def test_local_save_correction_round_trip(local_store):
    _assert_save_and_show(get_backend())


def test_dynamodb_save_correction_round_trip(aws):
    _assert_save_and_show(get_backend())


def _assert_save_without_context(backend) -> None:
    correction_id = backend.save_correction("111", "沒有上一輪對話的糾正", None, None)
    got = backend.list_corrections()[0]
    assert got["correction_id"] == correction_id
    assert got["context_question"] is None
    assert got["context_answer"] is None


def test_local_save_correction_without_context(local_store):
    _assert_save_without_context(get_backend())


def test_dynamodb_save_correction_without_context(aws):
    _assert_save_without_context(get_backend())


def _assert_status_state_machine(backend) -> None:
    pending_id = backend.save_correction("111", "correction A", None, None)
    other_id = backend.save_correction("222", "correction B", None, None)

    assert backend.update_correction_status(pending_id, "approved") is True
    assert backend.update_correction_status(other_id, "rejected") is True
    assert backend.update_correction_status("does-not-exist", "approved") is False

    pending = backend.list_corrections(status="pending")
    assert pending == []

    approved = backend.list_corrections(status="approved")
    assert [c["correction_id"] for c in approved] == [pending_id]

    rejected = backend.list_corrections(status="rejected")
    assert [c["correction_id"] for c in rejected] == [other_id]


def test_local_status_state_machine(local_store):
    _assert_status_state_machine(get_backend())


def test_dynamodb_status_state_machine(aws):
    _assert_status_state_machine(get_backend())


def _assert_limit(backend) -> None:
    for i in range(3):
        backend.save_correction("111", f"correction {i}", None, None)
    assert len(backend.list_corrections(limit=2)) == 2


def test_local_list_corrections_limit(local_store):
    _assert_limit(get_backend())


def test_dynamodb_list_corrections_limit(aws):
    _assert_limit(get_backend())
