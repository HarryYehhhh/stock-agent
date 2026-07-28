"""User-submitted correction persistence (ADR-0014).

Corrections are stored pending human review. Structural invariant: the
injection layer (agent.agent.run_agent()) never imports this module or reads
the corrections table — approved corrections only reach the model via a
maintainer manually pasting an `export` into knowledge/corrections.md and
merging a PR.
"""

from __future__ import annotations

from agent.db.backend import get_backend


def save_correction(
    chat_id: str,
    correction_text: str,
    context_question: str | None,
    context_answer: str | None,
) -> str:
    return get_backend().save_correction(chat_id, correction_text, context_question, context_answer)


def list_corrections(status: str | None = None, limit: int = 50) -> list[dict]:
    return get_backend().list_corrections(status, limit)


def update_correction_status(correction_id: str, status: str) -> bool:
    return get_backend().update_correction_status(correction_id, status)
