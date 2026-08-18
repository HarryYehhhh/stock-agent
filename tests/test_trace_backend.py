"""StorageBackend trace persistence (ADR-0013): save_trace / list_traces
round-trip parity between LocalSQLiteBackend and DynamoDBBackend.
"""

from __future__ import annotations

from agent.db.backend import get_backend


def _sample_trace(chat_id: str, ts: str, trace_id: str, outcome: str = "completed") -> dict:
    return {
        "chat_id": chat_id,
        "ts": ts,
        "trace_id": trace_id,
        "user_message": "AAPL 財報什麼時候",
        "fast_path": False,
        "playbook_hits": [{"source": "earnings#財報行事曆時間查詢", "score": 10.3}],
        "history_msg_count": 2,
        "history_char_count": 40,
        "tools": [
            {
                "iteration": 1,
                "name": "get_earnings_calendar",
                "args": '{"symbol": "AAPL"}',
                "duration_ms": 120,
                "result_type": "dict",
                "result_size": 3,
                "ok": True,
            }
        ],
        "iterations": 2,
        "usage": {"prompt_tokens": 500, "completion_tokens": 80, "total_tokens": 580},
        "usage_by_iteration": [
            {"prompt": 300, "completion": 0},
            {"prompt": 200, "completion": 80},
        ],
        "total_ms": 850,
        "outcome": outcome,
        "reply_char_count": 42,
        "model": "openai/gpt-oss-120b",
        "ttl": 1234567890,
        "bucket": "trace",
    }


def _assert_round_trip(backend) -> None:
    trace = _sample_trace("111", "2026-07-29T00:00:00+00:00", "trace-a")
    backend.save_trace(trace)

    results = backend.list_traces(chat_id="111")
    assert len(results) == 1
    got = results[0]
    assert got["chat_id"] == "111"
    assert got["trace_id"] == "trace-a"
    assert got["outcome"] == "completed"
    assert got["fast_path"] is False
    assert got["playbook_hits"] == [{"source": "earnings#財報行事曆時間查詢", "score": 10.3}]
    assert got["history_msg_count"] == 2
    assert got["history_char_count"] == 40
    assert got["tools"] == trace["tools"]
    assert got["iterations"] == 2
    assert got["usage"] == {"prompt_tokens": 500, "completion_tokens": 80, "total_tokens": 580}
    assert got["usage_by_iteration"] == trace["usage_by_iteration"]
    assert got["total_ms"] == 850
    assert got["reply_char_count"] == 42
    assert got["model"] == "openai/gpt-oss-120b"


def test_local_save_and_list_traces_round_trip(local_store):
    _assert_round_trip(get_backend())


def test_dynamodb_save_and_list_traces_round_trip(aws):
    _assert_round_trip(get_backend())


def _assert_filters(backend) -> None:
    backend.save_trace(_sample_trace("111", "2026-07-27T00:00:00+00:00", "t1"))
    backend.save_trace(_sample_trace("111", "2026-07-28T00:00:00+00:00", "t2"))
    backend.save_trace(_sample_trace("222", "2026-07-29T00:00:00+00:00", "t3"))

    # chat_id filter
    by_chat = backend.list_traces(chat_id="111")
    assert {t["trace_id"] for t in by_chat} == {"t1", "t2"}
    # newest first
    assert [t["trace_id"] for t in by_chat] == ["t2", "t1"]

    # since filter (global, no chat_id -> GSI on DynamoDB)
    since_results = backend.list_traces(since="2026-07-28T00:00:00+00:00")
    assert {t["trace_id"] for t in since_results} == {"t2", "t3"}

    # limit
    limited = backend.list_traces(limit=1)
    assert len(limited) == 1

    # no chat_id, no since -> all traces via global index/table
    all_results = backend.list_traces()
    assert {t["trace_id"] for t in all_results} == {"t1", "t2", "t3"}


def test_local_list_traces_filters(local_store):
    _assert_filters(get_backend())


def test_dynamodb_list_traces_filters(aws):
    _assert_filters(get_backend())
