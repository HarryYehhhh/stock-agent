"""agent.audit.judge: BM25 retrieval + Groq LLM-judge over conversations (ADR-0010)."""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.audit import dump, judge

DATE = "2026-07-27"


@pytest.fixture(autouse=True)
def _isolated_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(dump, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(dump, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(judge, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(judge, "AUDIT_DIR", tmp_path / "audit")
    return tmp_path


@pytest.fixture
def mock_groq(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(judge, "_client", lambda: client)
    return client


def _text_response(text: str):
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=42, completion_tokens=7),
    )


def _seed_earnings(aws):
    aws["ddb"].Table("test-earnings-calendar").put_item(
        Item={
            "symbol": "AAPL",
            "earning_date": "2026-08-05",
            "extended_hour": "bmo",
            "eps_estimate": Decimal("1.5"),
            "revenue_estimate": Decimal("94000000000"),
        }
    )


def _seed_message(aws, chat_id: str, ts: str, role: str, content: str):
    aws["ddb"].Table("test-conversations").put_item(
        Item={"chat_id": chat_id, "timestamp": ts, "role": role, "content": content}
    )


def _run_judge(**kwargs) -> int:
    defaults = {"date": DATE, "chat_id": None, "limit": 10, "top_k": 3}
    defaults.update(kwargs)
    return judge.run(argparse.Namespace(**defaults))


def _dump_earnings_snapshot():
    import argparse as ap

    parser = ap.ArgumentParser()
    dump.add_arguments(parser)
    args = parser.parse_args(["--date", DATE, "--table", "earnings"])
    assert dump.run(args) == 0


def test_judge_grounded_happy_path(aws, mock_groq):
    _seed_earnings(aws)
    _dump_earnings_snapshot()
    _seed_message(aws, "111", "2026-07-27T01:00:00+00:00", "user", "AAPL 什麼時候發財報")
    _seed_message(
        aws, "111", "2026-07-27T01:00:05+00:00", "assistant", "AAPL 預計 2026-08-05 盤前公布財報"
    )

    mock_groq.chat.completions.create.return_value = _text_response(
        json.dumps({"verdict": "grounded", "unsupported_claims": [], "reason": "日期與時段皆與快照相符"})
    )

    assert _run_judge() == 0

    mock_groq.chat.completions.create.assert_called_once()
    _, kwargs = mock_groq.chat.completions.create.call_args
    assert kwargs["temperature"] == 0
    assert kwargs["model"] == judge.MODEL

    report = (judge.AUDIT_DIR / DATE / "report.md").read_text(encoding="utf-8")
    assert "chat_id=111" in report
    assert "verdict：grounded" in report
    assert "earnings#AAPL" in report
    assert "grounded=1" in report
    assert "no-data=0" in report
    assert "ungrounded=0" in report


def test_judge_empty_hit_short_circuits_no_groq_call(aws, mock_groq):
    _seed_earnings(aws)
    _dump_earnings_snapshot()
    # Deliberately no token overlap with the AAPL snapshot content/heading.
    _seed_message(aws, "222", "2026-07-27T01:00:00+00:00", "user", "今天天氣如何")
    _seed_message(aws, "222", "2026-07-27T01:00:05+00:00", "assistant", "今天天氣晴朗適合出門")

    assert _run_judge(chat_id="222") == 0

    mock_groq.chat.completions.create.assert_not_called()
    report = (judge.AUDIT_DIR / DATE / "report.md").read_text(encoding="utf-8")
    assert "verdict：no-data" in report
    assert "no-data=1" in report


def test_judge_limit_takes_latest_n(aws, mock_groq):
    _seed_earnings(aws)
    _dump_earnings_snapshot()
    for i, ts_hour in enumerate(["01", "02", "03"]):
        chat_id = f"c{i}"
        _seed_message(aws, chat_id, f"2026-07-27T{ts_hour}:00:00+00:00", "user", "AAPL 財報")
        _seed_message(
            aws, chat_id, f"2026-07-27T{ts_hour}:00:05+00:00", "assistant", f"AAPL 財報 #{i}"
        )
    mock_groq.chat.completions.create.return_value = _text_response(
        json.dumps({"verdict": "grounded", "unsupported_claims": [], "reason": "ok"})
    )

    assert _run_judge(limit=1) == 0

    mock_groq.chat.completions.create.assert_called_once()
    report = (judge.AUDIT_DIR / DATE / "report.md").read_text(encoding="utf-8")
    assert "chat_id=c2" in report  # latest of the three
    assert "chat_id=c0" not in report
    assert "chat_id=c1" not in report


def test_judge_chat_id_filter(aws, mock_groq):
    _seed_earnings(aws)
    _dump_earnings_snapshot()
    _seed_message(aws, "aaa", "2026-07-27T01:00:00+00:00", "user", "AAPL 財報")
    _seed_message(aws, "aaa", "2026-07-27T01:00:05+00:00", "assistant", "AAPL 財報 A")
    _seed_message(aws, "bbb", "2026-07-27T02:00:00+00:00", "user", "AAPL 財報")
    _seed_message(aws, "bbb", "2026-07-27T02:00:05+00:00", "assistant", "AAPL 財報 B")
    mock_groq.chat.completions.create.return_value = _text_response(
        json.dumps({"verdict": "grounded", "unsupported_claims": [], "reason": "ok"})
    )

    assert _run_judge(chat_id="aaa") == 0

    assert mock_groq.chat.completions.create.call_count == 1
    report = (judge.AUDIT_DIR / DATE / "report.md").read_text(encoding="utf-8")
    assert "chat_id=aaa" in report
    assert "chat_id=bbb" not in report


def test_judge_missing_snapshot_reports_error_not_traceback(aws, mock_groq, capsys):
    # No dump run for DATE -> snapshots/<date>/earnings.md does not exist.
    assert _run_judge() == 1
    err = capsys.readouterr().err
    assert "找不到快照" in err
    assert "dump" in err
    mock_groq.chat.completions.create.assert_not_called()


def test_judge_dynamodb_scan_failure_reports_error(aws, mock_groq, monkeypatch, capsys):
    _seed_earnings(aws)
    _dump_earnings_snapshot()

    def _boom(_table):
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(judge, "_scan_all", _boom)

    assert _run_judge() == 1
    err = capsys.readouterr().err
    assert "DynamoDB scan failed" in err
    assert "scan exploded" in err


def test_judge_tolerant_json_parsing(aws, mock_groq):
    _seed_earnings(aws)
    _dump_earnings_snapshot()
    _seed_message(aws, "333", "2026-07-27T01:00:00+00:00", "user", "AAPL 財報")
    _seed_message(aws, "333", "2026-07-27T01:00:05+00:00", "assistant", "AAPL 財報 EPS $9.99")

    wrapped = (
        "這是我的判斷：\n"
        + json.dumps({"verdict": "ungrounded", "unsupported_claims": ["EPS $9.99 不在快照內"], "reason": "EPS 數字對不上"})
        + "\n謝謝。"
    )
    mock_groq.chat.completions.create.return_value = _text_response(wrapped)

    assert _run_judge() == 0

    report = (judge.AUDIT_DIR / DATE / "report.md").read_text(encoding="utf-8")
    assert "verdict：ungrounded" in report
    assert "EPS $9.99 不在快照內" in report
    assert "ungrounded=1" in report


def test_pair_qa_next_assistant_and_dangling_user_skipped():
    items = [
        {"chat_id": "1", "timestamp": "t1", "role": "user", "content": "Q1"},
        {"chat_id": "1", "timestamp": "t2", "role": "assistant", "content": "A1 (fast-path plain string)"},
        {"chat_id": "1", "timestamp": "t3", "role": "user", "content": "Q2 dangling, no assistant follows"},
    ]
    pairs = judge._pair_qa(items)
    assert len(pairs) == 1
    assert pairs[0]["question"] == "Q1"
    assert pairs[0]["answer"] == "A1 (fast-path plain string)"


def test_pair_qa_superseded_user_before_assistant_is_dropped():
    items = [
        {"chat_id": "1", "timestamp": "t1", "role": "user", "content": "Q1 gets superseded"},
        {"chat_id": "1", "timestamp": "t2", "role": "user", "content": "Q2 supersedes Q1"},
        {"chat_id": "1", "timestamp": "t3", "role": "assistant", "content": "A2"},
    ]
    pairs = judge._pair_qa(items)
    assert len(pairs) == 1
    assert pairs[0]["question"] == "Q2 supersedes Q1"


def test_pair_qa_ignores_non_user_assistant_roles():
    items = [
        {"chat_id": "1", "timestamp": "t1", "role": "user", "content": "Q1"},
        {"chat_id": "1", "timestamp": "t2", "role": "system", "content": "ignored"},
        {"chat_id": "1", "timestamp": "t3", "role": "assistant", "content": "A1"},
    ]
    pairs = judge._pair_qa(items)
    assert len(pairs) == 1
    assert pairs[0]["answer"] == "A1"


def test_extract_json_first_balanced_object():
    text = 'noise before {"verdict": "grounded", "unsupported_claims": [], "reason": "ok"} noise after'
    parsed = judge._extract_json(text)
    assert parsed == {"verdict": "grounded", "unsupported_claims": [], "reason": "ok"}


def test_extract_json_returns_none_when_no_braces():
    assert judge._extract_json("not json at all") is None
