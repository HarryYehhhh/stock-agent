"""ADR-0014 §2.3: approved corrections reach the model solely by a maintainer
pasting `export` output into knowledge/corrections.md — no new runtime
injection code. This verifies the existing search_knowledge_base /
agent.knowledge.retrieval machinery picks such a file up automatically once
it exists, and that this doesn't disturb retrieval over the real
knowledge/*.md seed files.
"""

from __future__ import annotations

import pytest

from agent.knowledge import retrieval
from agent.tools.knowledge_base_tool import search_knowledge_base

_CORRECTIONS_MD = """# Corrections 知識庫

## 使用者糾正 2026-07-29（test-1）

AAPL 財報應該是 8/1 盤後公布，不是 7/31 盤前。
"""


@pytest.fixture
def isolated_knowledge_dir(tmp_path, monkeypatch):
    (tmp_path / "corrections.md").write_text(_CORRECTIONS_MD, encoding="utf-8")
    monkeypatch.setattr(retrieval, "_KNOWLEDGE_DIR", tmp_path)
    retrieval._get_index.cache_clear()
    yield tmp_path
    retrieval._get_index.cache_clear()


def test_retrieve_hits_corrections_md_section_with_source_citation(isolated_knowledge_dir):
    results = retrieval.retrieve("AAPL 財報 8/1 盤後")

    assert results
    assert results[0].source == "corrections#使用者糾正 2026-07-29（test-1）"
    assert "8/1 盤後" in results[0].content


def test_search_knowledge_base_tool_surfaces_corrections_source(isolated_knowledge_dir):
    out = search_knowledge_base("AAPL 財報 8/1 盤後")

    assert out["results"]
    assert out["results"][0]["source"].startswith("corrections#")


def test_real_knowledge_seed_retrieval_unaffected_after_corrections_isolation():
    # No corrections.md exists in the real knowledge/ dir (isolated_knowledge_dir
    # not used here); the real index must still behave exactly as before this
    # sprint (see test_knowledge_retrieval.py's equivalent assertions).
    results = retrieval.retrieve("道瓊怎麼查")
    assert results
    assert results[0].source.startswith("indices#")
