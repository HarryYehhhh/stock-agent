"""agent.knowledge.debug: CLI entry point for inspecting BM25 retrieval
against either knowledge index (ADR-0009)."""

from __future__ import annotations

from agent.knowledge import debug


def test_debug_cli_default_source_queries_kb_index(capsys):
    debug.main(["道瓊怎麼查"])
    out = capsys.readouterr().out
    assert "indices#" in out


def test_debug_cli_playbook_source_queries_playbook_index(capsys):
    debug.main(["本週有什麼公司發布財報", "--source", "playbook"])
    out = capsys.readouterr().out
    assert "earnings#" in out


def test_debug_cli_prints_score_and_source_columns(capsys):
    debug.main(["道瓊怎麼查", "--top-k", "1"])
    out = capsys.readouterr().out.strip()
    lines = out.splitlines()
    assert len(lines) == 1
    score_str, source = lines[0].split("\t")
    assert float(score_str) > 0
    assert source.startswith("indices#")


def test_debug_cli_no_hits_prints_placeholder(capsys):
    debug.main(["今天天氣如何"])
    out = capsys.readouterr().out.strip()
    assert out == "(no hits)"
