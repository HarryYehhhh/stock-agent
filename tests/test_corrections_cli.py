"""python -m agent.corrections list/show/approve/reject/export CLI (ADR-0014)."""

from __future__ import annotations

from agent.corrections import __main__ as corrections_main
from agent.db.backend import get_backend


def _run(argv: list[str]) -> int:
    parser = corrections_main.build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def test_list_shows_pending_correction(local_store, capsys):
    correction_id = get_backend().save_correction("111", "AAPL 財報應該是 8/1 盤後", None, None)

    exit_code = _run(["list", "--status", "pending"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert correction_id in out
    assert "pending" in out


def test_list_empty_prints_placeholder(local_store, capsys):
    exit_code = _run(["list"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no corrections" in out


def test_show_prints_full_correction(local_store, capsys):
    correction_id = get_backend().save_correction(
        "111", "AAPL 財報應該是 8/1 盤後", "AAPL 財報什麼時候？", "AAPL 財報是 7/31 盤前"
    )

    exit_code = _run(["show", correction_id])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "AAPL 財報應該是 8/1 盤後" in out
    assert "AAPL 財報什麼時候？" in out
    assert "AAPL 財報是 7/31 盤前" in out


def test_show_missing_id_returns_error(local_store, capsys):
    exit_code = _run(["show", "does-not-exist"])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "not found" in out


def test_approve_updates_status(local_store, capsys):
    correction_id = get_backend().save_correction("111", "correction text", None, None)

    exit_code = _run(["approve", correction_id])

    assert exit_code == 0
    assert get_backend().list_corrections(status="approved")[0]["correction_id"] == correction_id
    assert get_backend().list_corrections(status="pending") == []


def test_reject_updates_status(local_store, capsys):
    correction_id = get_backend().save_correction("111", "correction text", None, None)

    exit_code = _run(["reject", correction_id])

    assert exit_code == 0
    assert get_backend().list_corrections(status="rejected")[0]["correction_id"] == correction_id


def test_export_produces_heading_section(local_store, capsys):
    correction_id = get_backend().save_correction(
        "111", "AAPL 財報應該是 8/1 盤後", "AAPL 財報什麼時候？", "AAPL 財報是 7/31 盤前"
    )
    get_backend().update_correction_status(correction_id, "approved")

    exit_code = _run(["export", "--status", "approved"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.startswith("## ")
    assert "AAPL 財報應該是 8/1 盤後" in out
    assert "AAPL 財報什麼時候？" in out

    # Exported output must parse into a `## ` section via the same parser
    # used by search_knowledge_base (ADR-0014 §2.3).
    from agent.knowledge.retrieval import parse_sections

    sections = parse_sections(out, "corrections")
    assert len(sections) == 1
    assert correction_id in sections[0].heading


def test_export_empty_status_prints_placeholder(local_store, capsys):
    exit_code = _run(["export", "--status", "approved"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "no corrections" in out
