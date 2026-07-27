"""CLI entry point: python -m agent.audit <dump|judge> [options].

See agent.audit (package docstring) and docs/decisions/0010-snapshot-audit-offline-llm-judge.md.
"""

from __future__ import annotations

import argparse
import logging

from agent.audit import dump, judge


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agent.audit", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    dump_parser = subparsers.add_parser(
        "dump", help="把 DynamoDB earnings-calendar / conversations dump 成帶日期的 markdown 快照"
    )
    dump.add_arguments(dump_parser)
    dump_parser.set_defaults(func=dump.run)

    judge_parser = subparsers.add_parser(
        "judge", help="用 BM25 檢索快照 + Groq LLM-judge 稽核既有對話"
    )
    judge.add_arguments(judge_parser)
    judge_parser.set_defaults(func=judge.run)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Mirrors chat_cli.py / local_server.py: configure logging only at the
    # CLI entrypoint (not at import time) so `judge`'s "groq usage" log line
    # (see agent.audit.judge, matching market_summary.py's pattern) is
    # actually visible when run as `python -m agent.audit`.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
