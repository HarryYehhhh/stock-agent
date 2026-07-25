"""Local knowledge base search tool: terminology, index/ticker mappings,
and analysis frameworks (see knowledge/*.md).

agent.knowledge.retrieval is stdlib-only and does not import the agent
package, so importing it at module level here is safe (no circular import,
unlike market_summary_tool.py -> agent.market_summary -> agent.agent).
"""

from __future__ import annotations

from agent.knowledge.retrieval import retrieve

_DEFAULT_TOP_K = 3
_MAX_TOP_K = 5
_MAX_CONTENT_CHARS = 1500
_TRUNCATE_SUFFIX = "...(內容過長，已截斷)"

KNOWLEDGE_BASE_TOOL = {
    "name": "search_knowledge_base",
    "description": (
        "Search the local knowledge base for terminology explanations (EPS "
        "beat/miss, guidance, bmo/amc/dmh), stock index / ticker mappings "
        "(e.g. Dow Jones/DJIA, S&P 500, Nasdaq have no directly quotable "
        "ticker — use ETF substitutes), and analysis methodology questions. "
        "Call this before guessing an index ticker for get_quote. Each "
        "result includes a source citation (file#heading) to cite in the reply."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query, e.g. '道瓊怎麼查' or 'EPS beat'",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results to return (default 3, max 5)",
            },
        },
        "required": ["query"],
    },
}


def _truncate(content: str) -> str:
    if len(content) <= _MAX_CONTENT_CHARS:
        return content
    return content[:_MAX_CONTENT_CHARS] + _TRUNCATE_SUFFIX


def search_knowledge_base(query: str, top_k: int = _DEFAULT_TOP_K) -> dict:
    k = max(0, min(top_k, _MAX_TOP_K))
    sections = retrieve(query, top_k=k)
    return {
        "results": [
            {"source": s.source, "heading": s.heading, "content": _truncate(s.content)}
            for s in sections
        ]
    }
