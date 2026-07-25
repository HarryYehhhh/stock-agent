"""Local Markdown knowledge base: tokenizer, section parsing and BM25 retrieval.

Stdlib-only (see ADR-0008). Must not import agent.agent / agent.market_summary /
agent.tools to avoid circular imports with the tools package.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_ASCII_RE = re.compile(r"[a-z0-9]")

# CJK Unified Ideographs (U+4E00-U+9FFF) + Extension A (U+3400-U+4DBF).
_CJK_RANGES = (("一", "鿿"), ("㐀", "䶿"))

# knowledge/ lives at the project root, three levels above this file
# (src/agent/knowledge/retrieval.py -> src/agent -> src -> <root>).
_KNOWLEDGE_DIR = Path(__file__).resolve().parents[3] / "knowledge"

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


def _load_sections() -> list[Section]:
    if not _KNOWLEDGE_DIR.is_dir():
        return []
    sections: list[Section] = []
    for path in sorted(_KNOWLEDGE_DIR.glob("*.md")):
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


def _build_index() -> _Index:
    sections = _load_sections()
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


@lru_cache(maxsize=1)
def _get_index() -> _Index:
    return _build_index()


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


def retrieve(query: str, top_k: int = 3) -> list[Section]:
    """Return up to top_k sections ranked by BM25 score, filtering out
    non-positive scores (no query token matched)."""
    index = _get_index()
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
    return [index.sections[i] for _, i in scored[:top_k]]
