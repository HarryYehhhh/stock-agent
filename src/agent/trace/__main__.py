"""CLI entry point: python -m agent.trace list [options].

Turns "查一次請求發生了什麼" from manual CloudWatch log correlation into a
table query against the `traces` table (see agent.agent.run_agent(), ADR-0013).

Usage:
    PYTHONPATH=src uv run python -m agent.trace list
    PYTHONPATH=src uv run python -m agent.trace list --chat-id 123 --since 2026-07-29
    PYTHONPATH=src uv run python -m agent.trace list --json
"""

from __future__ import annotations

import argparse
import json

from agent.db.backend import get_backend


def _print_table(traces: list[dict]) -> None:
    if not traces:
        print("(no traces)")
        return
    header = (
        f"{'ts':<26} {'chat_id':<16} {'outcome':<24} {'prompt':>7} {'completion':>10} "
        f"{'iter':>4} {'#tools':>6} {'total_ms':>9}"
    )
    print(header)
    for t in traces:
        usage = t.get("usage") or {}
        print(
            f"{t.get('ts', ''):<26} {t.get('chat_id', ''):<16} {t.get('outcome', ''):<24} "
            f"{usage.get('prompt_tokens', 0):>7} {usage.get('completion_tokens', 0):>10} "
            f"{t.get('iterations', 0):>4} {len(t.get('tools') or []):>6} {t.get('total_ms', 0):>9}"
        )


def _cmd_list(args: argparse.Namespace) -> int:
    traces = get_backend().list_traces(chat_id=args.chat_id, since=args.since, limit=args.limit)
    if args.json:
        print(json.dumps(traces, ensure_ascii=False, indent=2, default=str))
    else:
        _print_table(traces)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agent.trace", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="列出 request trace")
    list_parser.add_argument("--chat-id", default=None, help="限定 chat_id")
    list_parser.add_argument("--since", default=None, help="限定 ts >= ISO8601 時間字串")
    list_parser.add_argument("--limit", type=int, default=50, help="最多筆數（預設 50）")
    list_parser.add_argument("--json", action="store_true", help="輸出完整 JSON 紀錄")
    list_parser.set_defaults(func=_cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
