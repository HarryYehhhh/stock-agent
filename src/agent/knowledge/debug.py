"""Debug CLI: print top-k BM25 hits (with scores) for a query against one
of the two knowledge indexes (ADR-0009).

Usage:
    python -m agent.knowledge.debug "本週財報"                    # 術語 KB（knowledge/*.md），預設
    python -m agent.knowledge.debug "本週財報" --source playbook  # playbook 索引（knowledge/playbooks/）
    python -m agent.knowledge.debug "本週財報" --top-k 5

Stdlib-only (see ADR-0008): only imports agent.knowledge.retrieval.
"""

from __future__ import annotations

import argparse

from agent.knowledge.retrieval import _KNOWLEDGE_DIR, PLAYBOOKS_DIR, retrieve_scored


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="search query")
    parser.add_argument(
        "--source",
        choices=("kb", "playbook"),
        default="kb",
        help="which index to query: kb = knowledge/*.md (default), playbook = knowledge/playbooks/",
    )
    parser.add_argument("--top-k", type=int, default=3, help="max results to show (default 3)")
    args = parser.parse_args(argv)

    source_dir = PLAYBOOKS_DIR if args.source == "playbook" else _KNOWLEDGE_DIR
    results = retrieve_scored(args.query, top_k=args.top_k, source_dir=source_dir)

    if not results:
        print("(no hits)")
        return
    for section, score in results:
        print(f"{score:.3f}\t{section.source}")


if __name__ == "__main__":
    main()
