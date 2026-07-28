"""python -m agent.trace list CLI (ADR-0013)."""

from __future__ import annotations

import json

from agent.db.backend import get_backend
from agent.trace import __main__ as trace_main


def _run(argv: list[str]) -> int:
    parser = trace_main.build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def test_list_prints_table_for_saved_traces(local_store, capsys):
    get_backend().save_trace(
        {
            "chat_id": "111",
            "ts": "2026-07-29T00:00:00+00:00",
            "trace_id": "abc",
            "user_message": "AAPL 財報什麼時候",
            "fast_path": False,
            "playbook_hits": [],
            "history_msg_count": 0,
            "history_char_count": 0,
            "tools": [],
            "iterations": 1,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "usage_by_iteration": [{"prompt": 10, "completion": 5}],
            "total_ms": 100,
            "outcome": "completed",
            "reply_char_count": 20,
            "model": "llama-3.3-70b-versatile",
            "ttl": 1234567890,
            "bucket": "trace",
        }
    )

    exit_code = _run(["list"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "111" in out
    assert "completed" in out


def test_list_empty_prints_placeholder(local_store, capsys):
    exit_code = _run(["list"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no traces" in out


def test_list_json_outputs_full_record(local_store, capsys):
    get_backend().save_trace(
        {
            "chat_id": "111",
            "ts": "2026-07-29T00:00:00+00:00",
            "trace_id": "abc",
            "user_message": "AAPL 財報什麼時候",
            "fast_path": False,
            "playbook_hits": [{"source": "earnings#x", "score": 5.0}],
            "history_msg_count": 0,
            "history_char_count": 0,
            "tools": [],
            "iterations": 1,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "usage_by_iteration": [{"prompt": 10, "completion": 5}],
            "total_ms": 100,
            "outcome": "completed",
            "reply_char_count": 20,
            "model": "llama-3.3-70b-versatile",
            "ttl": 1234567890,
            "bucket": "trace",
        }
    )

    _run(["list", "--json"])

    out = capsys.readouterr().out
    records = json.loads(out)
    assert len(records) == 1
    assert records[0]["trace_id"] == "abc"
    assert records[0]["playbook_hits"] == [{"source": "earnings#x", "score": 5.0}]


def test_list_chat_id_filter(local_store, capsys):
    backend = get_backend()
    for chat_id, trace_id in (("111", "a"), ("222", "b")):
        backend.save_trace(
            {
                "chat_id": chat_id,
                "ts": "2026-07-29T00:00:00+00:00",
                "trace_id": trace_id,
                "user_message": "test",
                "fast_path": False,
                "playbook_hits": [],
                "history_msg_count": 0,
                "history_char_count": 0,
                "tools": [],
                "iterations": 1,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "usage_by_iteration": [],
                "total_ms": 0,
                "outcome": "completed",
                "reply_char_count": 0,
                "model": "llama-3.3-70b-versatile",
                "ttl": 1234567890,
                "bucket": "trace",
            }
        )

    _run(["list", "--chat-id", "111", "--json"])

    out = capsys.readouterr().out
    records = json.loads(out)
    assert len(records) == 1
    assert records[0]["chat_id"] == "111"
