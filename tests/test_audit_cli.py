"""agent.audit.__main__: argparse subcommand wiring and defaults (ADR-0010)."""

from __future__ import annotations

from agent.audit import __main__ as audit_main


def test_dump_subcommand_defaults():
    parser = audit_main.build_parser()
    args = parser.parse_args(["dump"])
    assert args.command == "dump"
    assert args.date is None
    assert args.table == "all"


def test_dump_subcommand_explicit_args():
    parser = audit_main.build_parser()
    args = parser.parse_args(["dump", "--date", "2026-07-27", "--table", "earnings"])
    assert args.date == "2026-07-27"
    assert args.table == "earnings"


def test_judge_subcommand_defaults():
    parser = audit_main.build_parser()
    args = parser.parse_args(["judge"])
    assert args.command == "judge"
    assert args.date is None
    assert args.chat_id is None
    assert args.limit == 10
    assert args.top_k == 3


def test_judge_subcommand_explicit_args():
    parser = audit_main.build_parser()
    args = parser.parse_args(
        ["judge", "--date", "2026-07-27", "--chat-id", "42", "--limit", "5", "--top-k", "2"]
    )
    assert args.date == "2026-07-27"
    assert args.chat_id == "42"
    assert args.limit == 5
    assert args.top_k == 2


def test_main_dispatches_to_dump_run(monkeypatch):
    calls = []
    monkeypatch.setattr(audit_main.dump, "run", lambda args: calls.append(args) or 0)

    exit_code = audit_main.main(["dump", "--table", "conversations"])

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0].table == "conversations"


def test_main_dispatches_to_judge_run(monkeypatch):
    calls = []
    monkeypatch.setattr(audit_main.judge, "run", lambda args: calls.append(args) or 3)

    exit_code = audit_main.main(["judge", "--limit", "1"])

    assert exit_code == 3
    assert len(calls) == 1
    assert calls[0].limit == 1
