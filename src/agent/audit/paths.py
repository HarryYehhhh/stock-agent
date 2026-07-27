"""Shared snapshot/audit directory paths (ADR-0010).

Two independent trees, both at the project root:
  snapshots/<date>/  -- data haystack (earnings.md), judge's retrieval source_dir.
  audit/<date>/       -- audit output (conversations.md, report.md).

Kept separate so retrieve_scored's non-recursive *.md glob over
snapshots/<date>/ never picks up conversations.md / report.md -- mixing
conversation content into the retrieval index would pollute the haystack
judge grounds its verdicts against.
"""

from __future__ import annotations

from pathlib import Path

# src/agent/audit/paths.py -> src/agent/audit -> src/agent -> src -> <root>
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOTS_DIR = _PROJECT_ROOT / "snapshots"
AUDIT_DIR = _PROJECT_ROOT / "audit"
