"""Local Markdown knowledge base: tokenizer, section parsing and BM25 retrieval.

Stdlib-only (see ADR-0008). Must not import agent.agent / agent.market_summary /
agent.tools to avoid circular imports with the tools package.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_ASCII_RE = re.compile(r"[a-z0-9]")

# CJK Unified Ideographs (U+4E00-U+9FFF) + Extension A (U+3400-U+4DBF).
_CJK_RANGES = (("一", "鿿"), ("㐀", "䶿"))


def _resolve_knowledge_dir(file_path: Path) -> Path:
    """Walk up from this module's own directory (the ``knowledge`` python
    package containing retrieval.py) to find the nearest ancestor that has a
    *different* ``knowledge/`` child directory -- the one holding the actual
    markdown content (``knowledge/*.md``, ``knowledge/playbooks/``,
    ``knowledge/exceptions/``).

    This is layout-agnostic, so it works for both known layouts without a
    magic parents[N] index: locally the content dir sits at the repo root
    (src/agent/knowledge/retrieval.py -> src/agent -> src -> <root>/knowledge,
    i.e. parents[3]); inside the Lambda container image (Dockerfile does
    ``COPY knowledge/ ./knowledge/``) it sits as a sibling of ``agent/``
    under ``${LAMBDA_TASK_ROOT}`` (agent/knowledge/retrieval.py -> agent ->
    /var/task/knowledge, i.e. parents[2]).
    """
    package_dir = file_path.resolve().parent
    for ancestor in package_dir.parents:
        candidate = ancestor / "knowledge"
        if candidate.is_dir() and candidate != package_dir:
            return candidate
    # No ancestor has a knowledge/ content dir. Note package_dir's own name
    # is already "knowledge" (it's this module's package dir), so a naive
    # "package_dir.parent / 'knowledge'" guess would just reconstruct
    # package_dir itself -- use a sentinel subpath that is guaranteed not
    # to exist instead, matching the old (pre-fix) silent-empty-index
    # behavior but with a warning.
    fallback = package_dir / "_not_found"
    log.warning(
        "knowledge/ content directory not found by walking up from %s; "
        "falling back to %s (BM25 indexes will be empty)",
        package_dir,
        fallback,
    )
    return fallback


_KNOWLEDGE_DIR = _resolve_knowledge_dir(Path(__file__))

# Independent index sources: knowledge/*.md (terminology KB, used by
# search_knowledge_base), knowledge/playbooks/*.md (tool-routing playbooks,
# pre-injected by run_agent, ADR-0009) and knowledge/exceptions/*.md
# (non-美股 off-topic example queries, used by run_agent's pre-LLM off-topic
# intercept). Kept as separate directories so the non-recursive glob() in
# _load_sections naturally never mixes them.
PLAYBOOKS_DIR = _KNOWLEDGE_DIR / "playbooks"
EXCEPTIONS_DIR = _KNOWLEDGE_DIR / "exceptions"

K1 = 1.5
B = 0.75
HEADING_WEIGHT = 2


def _is_cjk(ch: str) -> bool:
    return any(lo <= ch <= hi for lo, hi in _CJK_RANGES)


def tokenize(text: str) -> list[str]:
    """Lowercase ASCII word runs; CJK runs become adjacent-char bigrams
    (unigram if run length is 1); other characters are separators."""
    text = text.lower()
    tokens: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if _ASCII_RE.match(ch):
            j = i
            while j < n and _ASCII_RE.match(text[j]):
                j += 1
            tokens.append(text[i:j])
            i = j
        elif _is_cjk(ch):
            j = i
            while j < n and _is_cjk(text[j]):
                j += 1
            run = text[i:j]
            if len(run) == 1:
                tokens.append(run)
            else:
                tokens.extend(run[k : k + 2] for k in range(len(run) - 1))
            i = j
        else:
            i += 1
    return tokens


@dataclass(frozen=True)
class Section:
    source: str  # "<stem>#<heading>"
    heading: str
    content: str  # heading line + body, up to next "## " or EOF


def parse_sections(md_text: str, stem: str) -> list[Section]:
    """Split markdown text into level-2 (##) sections. Content before the
    first "## " heading (document title, preamble) is ignored. Deeper
    headings (###+) are folded into their parent ## section."""
    sections: list[Section] = []
    heading: str | None = None
    lines: list[str] = []

    def flush() -> None:
        if heading is not None:
            content = "\n".join(lines).strip()
            sections.append(Section(source=f"{stem}#{heading}", heading=heading, content=content))

    for line in md_text.splitlines():
        if line.startswith("## "):
            flush()
            heading = line.lstrip("#").strip()
            lines = [line]
        elif heading is not None:
            lines.append(line)
    flush()
    return sections


def _load_sections(source_dir: Path) -> list[Section]:
    """Load sections from all *.md files directly under source_dir
    (non-recursive, so knowledge/playbooks/ is never picked up by a
    glob over knowledge/*.md and vice versa)."""
    if not source_dir.is_dir():
        return []
    sections: list[Section] = []
    for path in sorted(source_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        sections.extend(parse_sections(text, path.stem))
    return sections


@dataclass(frozen=True)
class _Index:
    sections: list[Section]
    doc_tokens: list[list[str]]  # heading tokens counted twice + body tokens
    doc_len: list[int]
    avg_len: float
    doc_freq: dict[str, int]
    n: int


def _build_index(source_dir: Path) -> _Index:
    sections = _load_sections(source_dir)
    doc_tokens = [
        tokenize(s.heading) * HEADING_WEIGHT + tokenize(s.content) for s in sections
    ]
    doc_len = [len(tokens) for tokens in doc_tokens]
    avg_len = sum(doc_len) / len(doc_len) if doc_len else 0.0
    doc_freq: dict[str, int] = {}
    for tokens in doc_tokens:
        for term in set(tokens):
            doc_freq[term] = doc_freq.get(term, 0) + 1
    return _Index(
        sections=sections,
        doc_tokens=doc_tokens,
        doc_len=doc_len,
        avg_len=avg_len,
        doc_freq=doc_freq,
        n=len(sections),
    )


@lru_cache(maxsize=None)
def _get_index(source_dir: Path) -> _Index:
    # maxsize=None: only two distinct source_dir values ever exist in the
    # process (knowledge/ and knowledge/playbooks/), each cached independently
    # so the two indexes never share or pollute each other's state.
    return _build_index(source_dir)


def _idf(term: str, index: _Index) -> float:
    df = index.doc_freq.get(term, 0)
    return math.log((index.n - df + 0.5) / (df + 0.5) + 1)


def _bm25_score(query_tokens: list[str], doc_idx: int, index: _Index) -> float:
    tf_counts = Counter(index.doc_tokens[doc_idx])
    dl = index.doc_len[doc_idx]
    avgdl = index.avg_len or 1.0
    score = 0.0
    for term in query_tokens:
        tf = tf_counts.get(term, 0)
        if tf == 0:
            continue
        idf = _idf(term, index)
        score += idf * (tf * (K1 + 1)) / (tf + K1 * (1 - B + B * dl / avgdl))
    return score


def retrieve_scored(
    query: str, top_k: int = 3, source_dir: Path = _KNOWLEDGE_DIR
) -> list[tuple[Section, float]]:
    """Return up to top_k (Section, bm25_score) pairs ranked by score,
    filtering out non-positive scores (no query token matched). Scores
    are unnormalized BM25 (see K1/B/HEADING_WEIGHT above)."""
    index = _get_index(source_dir)
    if index.n == 0:
        return []
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    scored = [
        (_bm25_score(query_tokens, i, index), i)
        for i in range(index.n)
    ]
    scored = [(score, i) for score, i in scored if score > 0]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [(index.sections[i], score) for score, i in scored[:top_k]]


def retrieve(query: str, top_k: int = 3) -> list[Section]:
    """Return up to top_k sections ranked by BM25 score, filtering out
    non-positive scores (no query token matched). Unchanged behavior:
    searches the terminology KB (knowledge/*.md), used by
    search_knowledge_base."""
    return [section for section, _ in retrieve_scored(query, top_k, source_dir=_KNOWLEDGE_DIR)]
