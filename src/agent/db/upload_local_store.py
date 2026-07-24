"""Upload local sqlite storage rows to DynamoDB.

Run with:
    PYTHONPATH=src uv run python -m agent.db.upload_local_store
    PYTHONPATH=src uv run python -m agent.db.upload_local_store --write
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

import boto3

from agent.config import (
    aws_region,
    company_summaries_table,
    earnings_calendar_table,
    news_table,
)
from agent.db.backend import (
    COMPANY_SUMMARIES_TTL_SECONDS,
    EARNINGS_CALENDAR_TTL_SECONDS,
    NEWS_TTL_SECONDS,
    LocalSQLiteBackend,
    local_store_path,
    selected_backend_name,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Upload local sqlite storage to DynamoDB")
    parser.add_argument(
        "--write",
        action="store_true",
        help="write rows to DynamoDB; omitted mode is dry-run",
    )
    return parser


def _load_rows(path: Path) -> dict:
    if "LOCAL_STORE_PATH" not in os.environ:
        raise ValueError("LOCAL_STORE_PATH is required")
    if not path.exists():
        raise FileNotFoundError(f"LOCAL_STORE_PATH does not exist: {path}")
    return LocalSQLiteBackend(path).export_rows()


def _earnings_calendar_item(row: dict, ttl: int) -> dict:
    item: dict = {
        "symbol": row["symbol"],
        "earning_date": row["earning_date"],
        "updated_at": row["updated_at"],
        "ttl": ttl,
    }
    if row.get("extended_hour") is not None:
        item["extended_hour"] = row["extended_hour"]
    if row.get("eps_estimate") is not None:
        item["eps_estimate"] = Decimal(str(row["eps_estimate"]))
    if row.get("revenue_estimate") is not None:
        item["revenue_estimate"] = Decimal(str(row["revenue_estimate"]))
    return item


def _news_item(row: dict, ttl: int) -> dict:
    item: dict = {
        "headline": row["headline"],
        "bucket": "news",
        "published_at": row["published_at"],
        "category": row["category"],
        "summary": row["summary"],
        "fetched_at": row["fetched_at"],
        "ttl": ttl,
    }
    if row.get("symbol") is not None:
        item["symbol"] = row["symbol"]
    if row.get("url") is not None:
        item["url"] = row["url"]
    return item


def _write_rows(rows: dict) -> None:
    ddb = boto3.resource("dynamodb", region_name=aws_region())
    earnings_calendar = ddb.Table(earnings_calendar_table())
    company_summaries = ddb.Table(company_summaries_table())
    news = ddb.Table(news_table())

    now = int(time.time())
    for row in rows["earnings_calendar"]:
        earnings_calendar.put_item(
            Item=_earnings_calendar_item(row, now + EARNINGS_CALENDAR_TTL_SECONDS)
        )
    for row in rows["company_summaries"]:
        company_summaries.put_item(
            Item={
                "symbol": row["symbol"],
                "earning_date": row["earning_date"],
                "summary": row["summary"],
                "created_at": row["created_at"],
                "ttl": now + COMPANY_SUMMARIES_TTL_SECONDS,
            }
        )
    for row in rows["news"]:
        news.put_item(Item=_news_item(row, now + NEWS_TTL_SECONDS))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        selected_backend_name()
        rows = _load_rows(local_store_path())
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    counts = {name: len(items) for name, items in rows.items()}
    mode = "write" if args.write else "dry-run"
    print(
        f"{mode}: earnings_calendar={counts['earnings_calendar']} "
        f"company_summaries={counts['company_summaries']} "
        f"news={counts['news']}"
    )

    if not args.write:
        return 0

    try:
        _write_rows(rows)
    except Exception as exc:
        print(f"DynamoDB upload failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
