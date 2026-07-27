"""Dump DynamoDB tables into dated markdown snapshots (ADR-0010, ADR-0011).

- earnings-calendar -> snapshots/<date>/earnings.md (judge's retrieval haystack;
  format is compatible with agent.knowledge.retrieval's "## " section parser).
- news (NewsTable, both `news` and `market_summary` buckets) ->
  snapshots/<date>/news.md, same haystack tree as earnings.md (ADR-0011).
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
from agent.config import aws_region, conversations_table, earnings_calendar_table, news_table
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
        choices=("earnings", "conversations", "news", "all"),
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


_NEWS_SUMMARY_CHARS = 150

_UNKNOWN_DATE = "日期未知"


def _truncate_summary(text: str) -> str:
    text = text or ""
    if len(text) <= _NEWS_SUMMARY_CHARS:
        return text
    return text[:_NEWS_SUMMARY_CHARS] + "…（截斷）"


def _group_by_date(rows: list[dict], date_key_field: str | None = None) -> dict[str, list[dict]]:
    """Group rows by day. date_key_field selects an explicit date field
    (e.g. "summary_date"); otherwise the date is taken from
    published_at[:10]. Rows with no date fall into _UNKNOWN_DATE."""
    groups: dict[str, list[dict]] = {}
    for it in rows:
        if date_key_field:
            date_key = it.get(date_key_field) or _UNKNOWN_DATE
        else:
            published = it.get("published_at") or ""
            date_key = published[:10] if published else _UNKNOWN_DATE
        groups.setdefault(date_key, []).append(it)
    return groups


def _sorted_date_keys(keys) -> list[str]:
    """Date-descending (newest first); _UNKNOWN_DATE sorts last."""
    known = sorted((k for k in keys if k != _UNKNOWN_DATE), reverse=True)
    if _UNKNOWN_DATE in keys:
        known.append(_UNKNOWN_DATE)
    return known


def _sort_rows_by_headline(rows: list[dict]) -> list[dict]:
    """Same-day tiebreak is headline ascending (spec: "同日以 headline
    字母序 tiebreak")."""
    return sorted(rows, key=lambda it: it.get("headline", ""))


def _format_news_line(item: dict[str, Any]) -> str:
    # Date already lives in the section heading (symbol×date / date grouping).
    return f"- {item.get('headline', '')}：{_truncate_summary(item.get('summary', ''))}"


def _format_market_summary_line(item: dict[str, Any]) -> str:
    days_back = item.get("days_back", "?")
    return f"- （近 {days_back} 天）：{_truncate_summary(item.get('summary', ''))}"


def _dump_news(date_str: str) -> Path:
    table_name = news_table()
    ddb = boto3.resource("dynamodb", region_name=aws_region())
    items = _scan_all(ddb.Table(table_name))

    news_items = [it for it in items if it.get("bucket") == "news"]
    summary_items = [it for it in items if it.get("bucket") == "market_summary"]

    by_symbol: dict[str, list[dict]] = {}
    ai_items: list[dict] = []
    for it in news_items:
        symbol = it.get("symbol")
        if symbol:
            by_symbol.setdefault(symbol, []).append(it)
        else:
            ai_items.append(it)

    lines = [
        f"# 新聞快照 {date_str}",
        "",
        f"- 產生時間：{datetime.now(timezone.utc).isoformat()}",
        f"- 來源表：{table_name}",
        f"- 一般新聞筆數：{len(news_items)}",
        f"- 市場摘要筆數：{len(summary_items)}",
        "- 保留期：一般新聞近 ~14 天、市場摘要近 ~2 天",
        "- 每 symbol×日期一節（AI 產業新聞、市場摘要則每日期一節）",
    ]
    if not items:
        lines.append("- 近期無資料")
    lines.append("")

    for symbol in sorted(by_symbol):
        date_groups = _group_by_date(by_symbol[symbol])
        for date_key in _sorted_date_keys(date_groups.keys()):
            lines.append(f"## {symbol} 新聞 {date_key}")
            lines.extend(
                _format_news_line(it) for it in _sort_rows_by_headline(date_groups[date_key])
            )
            lines.append("")

    if ai_items:
        date_groups = _group_by_date(ai_items)
        for date_key in _sorted_date_keys(date_groups.keys()):
            lines.append(f"## AI 產業新聞 {date_key}")
            lines.extend(
                _format_news_line(it) for it in _sort_rows_by_headline(date_groups[date_key])
            )
            lines.append("")

    if summary_items:
        date_groups = _group_by_date(summary_items, date_key_field="summary_date")
        for date_key in _sorted_date_keys(date_groups.keys()):
            lines.append(f"## 市場摘要 {date_key}")
            lines.extend(
                _format_market_summary_line(it)
                for it in _sort_rows_by_headline(date_groups[date_key])
            )
            lines.append("")

    out_dir = SNAPSHOTS_DIR / date_str
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "news.md"
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
        if args.table in ("news", "all"):
            path = _dump_news(date_str)
            print(f"dumped news -> {path}")
        if args.table in ("conversations", "all"):
            path = _dump_conversations(date_str)
            print(f"dumped conversations -> {path}")
    except Exception as exc:
        print(f"DynamoDB scan failed: {exc}", file=sys.stderr)
        return 1

    return 0
