"""Dump DynamoDB tables into dated markdown snapshots (ADR-0010).

- earnings-calendar -> snapshots/<date>/earnings.md (judge's retrieval haystack;
  format is compatible with agent.knowledge.retrieval's "## " section parser).
- conversations -> audit/<date>/conversations.md (human-review dump only;
  `judge` reads Q/A straight from DynamoDB, not from this file -- see D2).

Run with:
    PYTHONPATH=src uv run python -m agent.audit dump
    PYTHONPATH=src uv run python -m agent.audit dump --date 2026-07-27 --table earnings
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3

from agent.audit.paths import AUDIT_DIR, SNAPSHOTS_DIR
from agent.config import aws_region, conversations_table, earnings_calendar_table
from agent.db.download_remote_store import _scan_all

# Duplicated from agent.fast_path._HOUR_LABELS (module-private there) rather
# than imported, matching that module's own precedent of small local
# duplication over cross-module coupling to another module's private symbols.
_HOUR_LABELS = {"bmo": "盤前", "amc": "盤後", "dmh": "盤中"}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--date", default=None, help="快照日期 YYYY-MM-DD（預設今天）"
    )
    parser.add_argument(
        "--table",
        choices=("earnings", "conversations", "all"),
        default="all",
        help="要 dump 的表（預設 all）",
    )


def _format_hour(hour: str | None) -> str:
    return _HOUR_LABELS.get(hour, "時段未定")


def _format_earnings_line(item: dict[str, Any]) -> str:
    fields: list[str] = []
    eps = item.get("eps_estimate")
    if eps is not None:
        fields.append(f"EPS 預估 ${eps:.2f}")
    revenue = item.get("revenue_estimate")
    if revenue is not None:
        fields.append(f"營收預估 {revenue / Decimal('1e9'):.1f}B")
    line = f"- {item['earning_date']}（{_format_hour(item.get('extended_hour'))}）"
    return line + "，".join(fields)


def _dump_earnings(date_str: str) -> Path:
    table_name = earnings_calendar_table()
    ddb = boto3.resource("dynamodb", region_name=aws_region())
    items = _scan_all(ddb.Table(table_name))

    by_symbol: dict[str, list[dict]] = {}
    for item in items:
        by_symbol.setdefault(item["symbol"], []).append(item)

    lines = [
        f"# 財報行事曆快照 {date_str}",
        "",
        f"- 產生時間：{datetime.now(timezone.utc).isoformat()}",
        f"- 來源表：{table_name}",
        f"- 筆數：{len(items)}",
        "",
    ]
    for symbol in sorted(by_symbol):
        lines.append(f"## {symbol}")
        rows = sorted(by_symbol[symbol], key=lambda it: it["earning_date"])
        lines.extend(_format_earnings_line(item) for item in rows)
        lines.append("")

    out_dir = SNAPSHOTS_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "earnings.md"
    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return out_path


def _dump_conversations(date_str: str) -> Path:
    table_name = conversations_table()
    ddb = boto3.resource("dynamodb", region_name=aws_region())
    items = _scan_all(ddb.Table(table_name))

    by_chat: dict[str, list[dict]] = {}
    for item in items:
        by_chat.setdefault(item["chat_id"], []).append(item)

    lines = [
        f"# 對話紀錄快照 {date_str}",
        "",
        f"- 產生時間：{datetime.now(timezone.utc).isoformat()}",
        f"- 來源表：{table_name}",
        f"- 筆數：{len(items)}",
        "",
    ]
    for chat_id in sorted(by_chat):
        lines.append(f"## chat_id={chat_id}")
        rows = sorted(by_chat[chat_id], key=lambda it: it["timestamp"])
        lines.extend(
            f"- {item['timestamp']} [{item['role']}] {item['content']}" for item in rows
        )
        lines.append("")

    out_dir = AUDIT_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "conversations.md"
    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return out_path


def run(args: argparse.Namespace) -> int:
    date_str = args.date or date.today().isoformat()

    try:
        if args.table in ("earnings", "all"):
            path = _dump_earnings(date_str)
            print(f"dumped earnings -> {path}")
        if args.table in ("conversations", "all"):
            path = _dump_conversations(date_str)
            print(f"dumped conversations -> {path}")
    except Exception as exc:
        print(f"DynamoDB scan failed: {exc}", file=sys.stderr)
        return 1

    return 0
