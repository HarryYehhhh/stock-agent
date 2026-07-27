"""agent.audit.dump: DynamoDB -> dated markdown snapshots (ADR-0010)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from agent.audit import dump
from agent.knowledge.retrieval import parse_sections, retrieve_scored

DATE = "2026-07-27"


@pytest.fixture(autouse=True)
def _isolated_dirs(monkeypatch, tmp_path):
    """Never write to the real repo snapshots/ or audit/ trees during tests."""
    monkeypatch.setattr(dump, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(dump, "AUDIT_DIR", tmp_path / "audit")
    return tmp_path


def _run_dump(table: str = "all", date_str: str = DATE) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    dump.add_arguments(parser)
    args = parser.parse_args(["--date", date_str, "--table", table])
    return dump.run(args)


def test_dump_earnings_format_and_sort(aws):
    table = aws["ddb"].Table("test-earnings-calendar")
    # Out-of-order inserts to prove output sorting (symbol asc, date asc
    # within symbol) is not just insertion order.
    table.put_item(
        Item={
            "symbol": "MSFT",
            "earning_date": "2026-08-01",
            "extended_hour": "amc",
            "eps_estimate": Decimal("2.5"),
            "revenue_estimate": Decimal("65000000000"),
        }
    )
    table.put_item(
        Item={
            "symbol": "AAPL",
            "earning_date": "2026-08-05",
            "extended_hour": "bmo",
            "eps_estimate": Decimal("1.5"),
            "revenue_estimate": Decimal("94000000000"),
        }
    )
    table.put_item(
        Item={
            "symbol": "AAPL",
            "earning_date": "2026-05-01",
            # no extended_hour -> "時段未定"
            "eps_estimate": Decimal("1.2"),
        }
    )

    assert _run_dump(table="earnings") == 0

    out_path = dump.SNAPSHOTS_DIR / DATE / "earnings.md"
    assert out_path.is_file()
    text = out_path.read_text(encoding="utf-8")

    # Metadata lives before the first "## " heading.
    head = text.split("## ", 1)[0]
    assert "產生時間" in head
    assert "來源表：test-earnings-calendar" in head
    assert "筆數：3" in head

    sections = parse_sections(text, "earnings")
    headings = [s.heading for s in sections]
    assert headings == ["AAPL", "MSFT"]  # symbol alphabetical order

    aapl_section = sections[0]
    # Date ascending within symbol: 2026-05-01 before 2026-08-05.
    idx_may = aapl_section.content.index("2026-05-01")
    idx_aug = aapl_section.content.index("2026-08-05")
    assert idx_may < idx_aug
    assert "時段未定" in aapl_section.content  # missing extended_hour
    assert "盤前" in aapl_section.content
    assert "EPS 預估 $1.50" in aapl_section.content
    assert "營收預估 94.0B" in aapl_section.content
    # Missing revenue_estimate -> field omitted entirely, no stray "None".
    assert "None" not in aapl_section.content
    assert "營收預估" not in aapl_section.content.split("2026-05-01")[1].split("\n")[0]

    msft_section = sections[1]
    assert "盤後" in msft_section.content
    assert "EPS 預估 $2.50" in msft_section.content
    assert "營收預估 65.0B" in msft_section.content


def test_dump_earnings_snapshot_is_retrievable(aws):
    table = aws["ddb"].Table("test-earnings-calendar")
    table.put_item(
        Item={
            "symbol": "NVDA",
            "earning_date": "2026-08-20",
            "extended_hour": "amc",
            "eps_estimate": Decimal("0.8"),
            "revenue_estimate": Decimal("30000000000"),
        }
    )

    assert _run_dump(table="earnings") == 0

    hits = retrieve_scored("NVDA 財報", top_k=3, source_dir=dump.SNAPSHOTS_DIR / DATE)
    assert hits
    assert hits[0][0].source == "earnings#NVDA"


def test_dump_conversations_groups_and_orders(aws):
    table = aws["ddb"].Table("test-conversations")
    # Out-of-order inserts across two chat_ids.
    table.put_item(
        Item={"chat_id": "222", "timestamp": "2026-07-27T02:00:00+00:00", "role": "assistant", "content": "B2"}
    )
    table.put_item(
        Item={"chat_id": "111", "timestamp": "2026-07-27T01:00:00+00:00", "role": "user", "content": "A1"}
    )
    table.put_item(
        Item={"chat_id": "111", "timestamp": "2026-07-27T00:00:00+00:00", "role": "user", "content": "A0"}
    )
    table.put_item(
        Item={"chat_id": "222", "timestamp": "2026-07-27T01:00:00+00:00", "role": "user", "content": "B1"}
    )

    assert _run_dump(table="conversations") == 0

    out_path = dump.AUDIT_DIR / DATE / "conversations.md"
    assert out_path.is_file()
    text = out_path.read_text(encoding="utf-8")

    assert "筆數：4" in text.split("## ", 1)[0]

    # chat_id grouping order + within-group ascending timestamp order.
    idx_111 = text.index("## chat_id=111")
    idx_222 = text.index("## chat_id=222")
    assert idx_111 < idx_222
    block_111 = text[idx_111:idx_222]
    assert block_111.index("A0") < block_111.index("A1")


def test_dump_creates_missing_directories(aws, tmp_path):
    assert not (dump.SNAPSHOTS_DIR / DATE).exists()
    assert not (dump.AUDIT_DIR / DATE).exists()

    assert _run_dump(table="all") == 0

    assert (dump.SNAPSHOTS_DIR / DATE / "earnings.md").is_file()
    assert (dump.AUDIT_DIR / DATE / "conversations.md").is_file()


def test_dump_scan_failure_reports_error_no_traceback(aws, monkeypatch, capsys):
    def _boom(_table):
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(dump, "_scan_all", _boom)

    assert _run_dump(table="earnings") == 1
    err = capsys.readouterr().err
    assert "DynamoDB scan failed" in err
    assert "scan exploded" in err
