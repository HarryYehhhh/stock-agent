"""Upload local sqlite storage rows to DynamoDB.

Run with:
    PYTHONPATH=src uv run python -m agent.db.upload_local_store
    PYTHONPATH=src uv run python -m agent.db.upload_local_store --write
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3

from agent.config import (
    aws_region,
    conversations_table,
    processed_updates_table,
    subscribers_table,
    watchlist_table,
)
from agent.db.backend import LocalSQLiteBackend, local_store_path, selected_backend_name


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


def _write_rows(rows: dict) -> None:
    ddb = boto3.resource("dynamodb", region_name=aws_region())
    conversations = ddb.Table(conversations_table())
    subscribers = ddb.Table(subscribers_table())
    dedup = ddb.Table(processed_updates_table())
    watchlist = ddb.Table(watchlist_table())

    for item in rows["conversations"]:
        conversations.put_item(Item=item)
    for item in rows["subscribers"]:
        subscribers.put_item(Item=item)
    for item in rows["dedup"]:
        dedup.put_item(Item=item)
    for item in rows["watchlist"]:
        watchlist.put_item(Item=item)


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
        f"{mode}: conversations={counts['conversations']} "
        f"subscribers={counts['subscribers']} dedup={counts['dedup']} "
        f"watchlist={counts['watchlist']}"
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
