"""Mirror DynamoDB watchlist + subscribers into local sqlite (full overwrite).

DynamoDB is the source of truth for Telegram-input tables (watchlist,
subscribers); this module scans both fully, then overwrites the local sqlite
copies in a single transaction. See docs/decisions/0005-dynamodb-source-of-truth-downsync.md.

Run with:
    PYTHONPATH=src uv run python -m agent.db.download_remote_store
    PYTHONPATH=src uv run python -m agent.db.download_remote_store --write
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Any

import boto3

from agent.config import aws_region, subscribers_table, watchlist_table
from agent.db.backend import LocalSQLiteBackend, local_store_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Mirror DynamoDB watchlist + subscribers into local sqlite"
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="overwrite local sqlite tables; omitted mode is dry-run",
    )
    return parser


def _scan_all(table: Any) -> list[dict]:
    items: list[dict] = []
    resp = table.scan()
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


def _watchlist_row(item: dict) -> tuple[str, str, int]:
    return (item["chat_id"], item["symbol"], int(item["position"]))


def _subscriber_row(item: dict) -> tuple[str, str, int]:
    subscribed_at = item.get("subscribed_at") or datetime.now(timezone.utc).isoformat()
    return (item["chat_id"], subscribed_at, 1 if item.get("active") else 0)


def _overwrite_local_store(
    watchlist_items: list[dict], subscriber_items: list[dict]
) -> None:
    backend = LocalSQLiteBackend(local_store_path())
    with backend._connect() as conn:
        conn.execute("DELETE FROM watchlist")
        conn.executemany(
            "INSERT INTO watchlist (chat_id, symbol, position) VALUES (?, ?, ?)",
            [_watchlist_row(item) for item in watchlist_items],
        )
        conn.execute("DELETE FROM subscribers")
        conn.executemany(
            "INSERT INTO subscribers (chat_id, subscribed_at, active) VALUES (?, ?, ?)",
            [_subscriber_row(item) for item in subscriber_items],
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    ddb = boto3.resource("dynamodb", region_name=aws_region())
    try:
        watchlist_items = _scan_all(ddb.Table(watchlist_table()))
        subscriber_items = _scan_all(ddb.Table(subscribers_table()))
    except Exception as exc:
        print(f"DynamoDB scan failed: {exc}", file=sys.stderr)
        return 1

    mode = "write" if args.write else "dry-run"
    print(f"{mode}: watchlist={len(watchlist_items)} subscribers={len(subscriber_items)}")

    if not args.write:
        return 0

    try:
        _overwrite_local_store(watchlist_items, subscriber_items)
    except Exception as exc:
        print(f"local sqlite overwrite failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
