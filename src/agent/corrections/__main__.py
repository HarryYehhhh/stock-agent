"""CLI entry point: python -m agent.corrections <list|show|approve|reject|export> [options].

Manage user-submitted corrections (ADR-0014). `export` prints approved
corrections as paste-ready `## ` markdown sections for knowledge/corrections.md
— the only way approved corrections can reach the model is a maintainer
pasting that output into the file and merging a PR (no runtime auto-injection).

Usage:
    PYTHONPATH=src uv run python -m agent.corrections list --status pending
    PYTHONPATH=src uv run python -m agent.corrections show <correction_id>
    PYTHONPATH=src uv run python -m agent.corrections approve <correction_id>
    PYTHONPATH=src uv run python -m agent.corrections reject <correction_id>
    PYTHONPATH=src uv run python -m agent.corrections export --status approved
"""

from __future__ import annotations

import argparse

from agent.db.backend import get_backend

_SUMMARY_CHARS = 60


def _truncate(text: str | None, limit: int = _SUMMARY_CHARS) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…"


def _find_correction(correction_id: str) -> dict | None:
    # No get-by-id method on StorageBackend (Protocol only has save/list/
    # update); scale is small (pending corrections are reviewed by hand),
    # matching DynamoDBBackend.get_all_active()'s precedent of a full scan
    # being acceptable at this volume.
    for correction in get_backend().list_corrections(limit=10_000):
        if correction["correction_id"] == correction_id:
            return correction
    return None


def _cmd_list(args: argparse.Namespace) -> int:
    corrections = get_backend().list_corrections(status=args.status, limit=args.limit)
    if not corrections:
        print("(no corrections)")
        return 0
    for c in corrections:
        print(
            f"{c['correction_id']}\t{c['created_at']}\t{c['status']}\t"
            f"{_truncate(c['correction_text'])}"
        )
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    correction = _find_correction(args.correction_id)
    if correction is None:
        print(f"correction not found: {args.correction_id}")
        return 1
    print(f"correction_id: {correction['correction_id']}")
    print(f"chat_id: {correction['chat_id']}")
    print(f"created_at: {correction['created_at']}")
    print(f"status: {correction['status']}")
    print(f"correction_text: {correction['correction_text']}")
    print(f"context_question: {correction.get('context_question')}")
    print(f"context_answer: {correction.get('context_answer')}")
    return 0


def _set_status(correction_id: str, status: str) -> int:
    updated = get_backend().update_correction_status(correction_id, status)
    if not updated:
        print(f"correction not found: {correction_id}")
        return 1
    print(f"{status}: {correction_id}")
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    return _set_status(args.correction_id, "approved")


def _cmd_reject(args: argparse.Namespace) -> int:
    return _set_status(args.correction_id, "rejected")


def _export_section(correction: dict) -> str:
    lines = [f"## 使用者糾正 {correction['created_at']}（{correction['correction_id']}）", ""]
    lines.append(correction["correction_text"])
    context_question = correction.get("context_question")
    context_answer = correction.get("context_answer")
    if context_question or context_answer:
        lines.append("")
        lines.append(f"（原問題：{context_question or ''}／原回覆：{context_answer or ''}）")
    return "\n".join(lines)


def _cmd_export(args: argparse.Namespace) -> int:
    corrections = get_backend().list_corrections(status=args.status, limit=args.limit)
    if not corrections:
        print("(no corrections to export)")
        return 0
    print("\n\n".join(_export_section(c) for c in corrections))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m agent.corrections", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="列出 corrections")
    list_parser.add_argument(
        "--status", default=None, choices=("pending", "approved", "rejected")
    )
    list_parser.add_argument("--limit", type=int, default=50)
    list_parser.set_defaults(func=_cmd_list)

    show_parser = subparsers.add_parser("show", help="顯示單筆 correction 完整內容")
    show_parser.add_argument("correction_id")
    show_parser.set_defaults(func=_cmd_show)

    approve_parser = subparsers.add_parser("approve", help="核准一筆 correction")
    approve_parser.add_argument("correction_id")
    approve_parser.set_defaults(func=_cmd_approve)

    reject_parser = subparsers.add_parser("reject", help="駁回一筆 correction")
    reject_parser.add_argument("correction_id")
    reject_parser.set_defaults(func=_cmd_reject)

    export_parser = subparsers.add_parser(
        "export", help="輸出可貼進 knowledge/corrections.md 的 markdown"
    )
    export_parser.add_argument(
        "--status", default="approved", choices=("pending", "approved", "rejected")
    )
    export_parser.add_argument("--limit", type=int, default=50)
    export_parser.set_defaults(func=_cmd_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
