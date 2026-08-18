"""Storage backend selection, local sqlite implementation, and DynamoDB implementation."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from agent.config import (
    aws_region,
    company_summaries_table,
    conversations_table,
    corrections_table,
    earnings_calendar_table,
    earnings_notifications_table,
    news_table,
    processed_updates_table,
    rate_limits_table,
    subscribers_table,
    traces_table,
    watchlist_table,
)

CONVERSATION_TTL_SECONDS = 30 * 24 * 3600
DEDUP_TTL_SECONDS = 3600
EARNINGS_CALENDAR_TTL_SECONDS = 45 * 24 * 3600
EARNINGS_NOTIFICATIONS_TTL_SECONDS = 30 * 24 * 3600
COMPANY_SUMMARIES_TTL_SECONDS = 30 * 24 * 3600
NEWS_TTL_SECONDS = 14 * 24 * 3600
MARKET_SUMMARY_TTL_SECONDS = 2 * 24 * 3600
CORRECTIONS_TTL_SECONDS = 90 * 24 * 3600
DEFAULT_LOCAL_STORE_PATH = ".local/earnings-agent.sqlite3"


def selected_backend_name() -> str:
    name = os.getenv("STORAGE_BACKEND")
    if not name:
        name = "dynamodb" if os.getenv("AWS_LAMBDA_FUNCTION_NAME") else "local"
    name = name.lower()
    if name not in {"local", "dynamodb"}:
        raise ValueError(f"Unsupported STORAGE_BACKEND: {name}")
    return name


def local_store_path() -> Path:
    return Path(os.getenv("LOCAL_STORE_PATH", DEFAULT_LOCAL_STORE_PATH))


class StorageBackend(Protocol):
    """Cross-backend storage interface (conversations / subscribers / dedup /
    watchlist / earnings cache / notification idempotency / company summaries /
    news / market summary persistence).

    These methods are implemented by both `LocalSQLiteBackend` and
    `DynamoDBBackend` (ADR-0003 established the Protocol; ADR-0004 extends its
    scope to watchlist/earnings-cache/notifications/company-summaries; ADR-0006
    further extends it to news / market-summary persistence now that the daily
    pipeline runs against both backends). `export_rows` remains local-only and
    is intentionally excluded — it is only used by the local→DynamoDB upload
    tool.

    `summary_notified` / `mark_summary_notified` reuse the earnings
    notification-idempotency table to dedupe daily market-summary pushes
    (ADR-0007 §4).
    """

    def save_message(self, chat_id: str, role: str, content: str) -> None: ...

    def get_history(self, chat_id: str, limit: int) -> list[dict]: ...

    def subscribe(self, chat_id: str) -> None: ...

    def unsubscribe(self, chat_id: str) -> None: ...

    def get_all_active(self) -> list[str]: ...

    def claim_update_id(self, update_id: int) -> bool: ...

    def increment_rate_limit(self, chat_id: str, window_key: str, ttl: int) -> int: ...

    def mark_rate_limit_notified(self, chat_id: str, window_key: str) -> bool: ...

    def get_watchlist(self, chat_id: str) -> list[dict]: ...

    def add_to_watchlist(self, chat_id: str, symbol: str) -> bool: ...

    def remove_from_watchlist(self, chat_id: str, symbol: str) -> bool: ...

    def seed_watchlist_if_empty(self, chat_id: str) -> None: ...

    def upsert_earnings_calendar(self, items: list[dict]) -> None: ...

    def delete_earnings_calendar_except(
        self, symbol: str, keep_dates: list[str], date_from: str
    ) -> None: ...

    def get_active_watchlist_symbols(self) -> list[dict]: ...

    def get_watchlist_earnings_for_date(self, earning_date: str) -> list[dict]: ...

    def get_chat_watchlist_earnings(
        self, chat_id: str, date_from: str, date_to: str
    ) -> list[dict]: ...

    def unnotified_symbols(
        self, chat_id: str, symbols: list[str], earning_date: str
    ) -> list[str]: ...

    def mark_notified(
        self, chat_id: str, symbols: list[str], earning_date: str
    ) -> None: ...

    def summary_notified(self, chat_id: str, summary_date: str) -> bool: ...

    def mark_summary_notified(self, chat_id: str, summary_date: str) -> None: ...

    def get_company_summary(self, symbol: str, earning_date: str) -> str | None: ...

    def upsert_company_summary(
        self, symbol: str, earning_date: str, summary: str
    ) -> None: ...

    def insert_news(self, items: list[dict]) -> int: ...

    def list_news_since(self, published_from: str) -> list[dict]: ...

    def insert_market_summary(
        self,
        summary: str,
        news_ids: list,
        summary_date: str,
        days_back: int,
        news_ids_hash: str,
    ) -> Any: ...

    def get_cached_market_summary(
        self, summary_date: str, days_back: int, news_ids_hash: str
    ) -> dict | None: ...

    def save_trace(self, trace: dict) -> None: ...

    def list_traces(
        self, chat_id: str | None = None, since: str | None = None, limit: int = 50
    ) -> list[dict]: ...

    def save_correction(
        self,
        chat_id: str,
        correction_text: str,
        context_question: str | None,
        context_answer: str | None,
    ) -> str: ...

    def list_corrections(self, status: str | None = None, limit: int = 50) -> list[dict]: ...

    def update_correction_status(self, correction_id: str, status: str) -> bool: ...


@lru_cache(maxsize=1)
def get_backend() -> StorageBackend:
    if selected_backend_name() == "local":
        return LocalSQLiteBackend(local_store_path())
    return DynamoDBBackend()


class LocalSQLiteBackend:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    ttl INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subscribers (
                    chat_id TEXT PRIMARY KEY,
                    subscribed_at TEXT NOT NULL,
                    active INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_updates (
                    update_id INTEGER PRIMARY KEY,
                    ttl INTEGER NOT NULL
                )
                """
            )
            self._migrate_watchlist_schema(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist (
                    chat_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY (chat_id, symbol)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_watchlist_symbol
                ON watchlist (symbol)
                """
            )
            # Migrate pre-existing per-subscriber DBs that still carry exchange.
            watchlist_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(watchlist)")
            }
            if "exchange" in watchlist_columns:
                conn.execute("ALTER TABLE watchlist DROP COLUMN exchange")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS earnings_calendar (
                    symbol TEXT NOT NULL,
                    earning_date TEXT NOT NULL,
                    extended_hour TEXT,
                    eps_estimate REAL,
                    revenue_estimate REAL,
                    updated_at TEXT,
                    PRIMARY KEY (symbol, earning_date)
                )
                """
            )
            # Migrate pre-existing DBs created before updated_at existed.
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(earnings_calendar)")
            }
            if "updated_at" not in columns:
                conn.execute(
                    "ALTER TABLE earnings_calendar ADD COLUMN updated_at TEXT"
                )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_earnings_date
                ON earnings_calendar (earning_date)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    symbol TEXT,
                    headline TEXT NOT NULL UNIQUE,
                    summary TEXT NOT NULL,
                    url TEXT,
                    published_at TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    source TEXT,
                    high_credibility INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # Migrate pre-existing DBs created before source/high_credibility
            # existed. Old rows get source=NULL, high_credibility=0.
            news_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(news)")
            }
            if "source" not in news_columns:
                conn.execute("ALTER TABLE news ADD COLUMN source TEXT")
            if "high_credibility" not in news_columns:
                conn.execute(
                    "ALTER TABLE news ADD COLUMN high_credibility INTEGER NOT NULL DEFAULT 0"
                )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_news_published
                ON news (published_at)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    news_ids TEXT NOT NULL
                )
                """
            )
            # Migrate pre-existing DBs created before the cache columns
            # existed. Old rows keep these NULL, so they never match a cache
            # lookup (summary_date/days_back/news_ids_hash all compared with
            # `=`) and effectively expire on their own.
            market_summaries_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(market_summaries)")
            }
            if "summary_date" not in market_summaries_columns:
                conn.execute("ALTER TABLE market_summaries ADD COLUMN summary_date TEXT")
            if "days_back" not in market_summaries_columns:
                conn.execute("ALTER TABLE market_summaries ADD COLUMN days_back INTEGER")
            if "news_ids_hash" not in market_summaries_columns:
                conn.execute("ALTER TABLE market_summaries ADD COLUMN news_ids_hash TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS earnings_notifications (
                    chat_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    earning_date TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, symbol, earning_date)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS company_summaries (
                    symbol TEXT NOT NULL,
                    earning_date TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, earning_date)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_limits (
                    chat_id TEXT NOT NULL,
                    window_key TEXT NOT NULL,
                    hit_count INTEGER NOT NULL,
                    notified INTEGER NOT NULL DEFAULT 0,
                    ttl INTEGER NOT NULL,
                    PRIMARY KEY (chat_id, window_key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    user_message TEXT NOT NULL,
                    fast_path INTEGER NOT NULL,
                    playbook_hits TEXT NOT NULL,
                    history_msg_count INTEGER NOT NULL,
                    history_char_count INTEGER NOT NULL,
                    tools TEXT NOT NULL,
                    iterations INTEGER NOT NULL,
                    usage TEXT NOT NULL,
                    usage_by_iteration TEXT NOT NULL,
                    total_ms INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    reply_char_count INTEGER NOT NULL,
                    model TEXT,
                    ttl INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_traces_chat_id ON traces (chat_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_traces_ts ON traces (ts)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS corrections (
                    correction_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    correction_text TEXT NOT NULL,
                    context_question TEXT,
                    context_answer TEXT,
                    status TEXT NOT NULL,
                    ttl INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_corrections_status ON corrections (status)"
            )

    def _migrate_watchlist_schema(self, conn: sqlite3.Connection) -> None:
        """One-time migration from the old global watchlist to per-subscriber rows.

        Old shape: watchlist(ticker PRIMARY KEY, exchange, position) — a single
        global list. New shape: watchlist(chat_id, symbol, position),
        PRIMARY KEY (chat_id, symbol). Detected by absence of a chat_id column;
        idempotent because the new shape short-circuits this method.
        """
        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'watchlist'"
        ).fetchone()
        if not table_exists:
            return

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(watchlist)")}
        if "chat_id" in columns:
            return

        old_rows = conn.execute("SELECT ticker, position FROM watchlist").fetchall()
        subscriber_ids = [
            row["chat_id"] for row in conn.execute("SELECT chat_id FROM subscribers")
        ]

        conn.execute("DROP TABLE watchlist")
        conn.execute(
            """
            CREATE TABLE watchlist (
                chat_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (chat_id, symbol)
            )
            """
        )

        if subscriber_ids and old_rows:
            conn.executemany(
                """
                INSERT INTO watchlist (chat_id, symbol, position)
                VALUES (?, ?, ?)
                """,
                [
                    (chat_id, row["ticker"], row["position"])
                    for chat_id in subscriber_ids
                    for row in old_rows
                ],
            )

    def save_message(self, chat_id: str, role: str, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (chat_id, timestamp, role, content, ttl)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    datetime.now(timezone.utc).isoformat(),
                    role,
                    content,
                    int(time.time()) + CONVERSATION_TTL_SECONDS,
                ),
            )

    def get_history(self, chat_id: str, limit: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT chat_id, timestamp, role, content, ttl
                FROM (
                    SELECT chat_id, timestamp, role, content, ttl, id
                    FROM conversations
                    WHERE chat_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (chat_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def subscribe(self, chat_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO subscribers (chat_id, subscribed_at, active)
                VALUES (?, ?, 1)
                ON CONFLICT(chat_id) DO UPDATE SET
                    subscribed_at = excluded.subscribed_at,
                    active = 1
                """,
                (chat_id, datetime.now(timezone.utc).isoformat()),
            )

    def unsubscribe(self, chat_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO subscribers (chat_id, subscribed_at, active)
                VALUES (?, ?, 0)
                ON CONFLICT(chat_id) DO UPDATE SET active = 0
                """,
                (chat_id, datetime.now(timezone.utc).isoformat()),
            )

    def get_all_active(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT chat_id FROM subscribers WHERE active = 1 ORDER BY chat_id"
            ).fetchall()
        return [row["chat_id"] for row in rows]

    def get_watchlist(self, chat_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT symbol
                FROM watchlist
                WHERE chat_id = ?
                ORDER BY position
                """,
                (chat_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_to_watchlist(self, chat_id: str, symbol: str) -> bool:
        """Add symbol (uppercased) to chat_id's watchlist. No-op if already present."""
        symbol = symbol.upper()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM watchlist WHERE chat_id = ? AND symbol = ?",
                (chat_id, symbol),
            ).fetchone()
            if existing:
                return False
            max_position = conn.execute(
                "SELECT MAX(position) AS max_position FROM watchlist WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()["max_position"]
            next_position = 0 if max_position is None else max_position + 1
            conn.execute(
                """
                INSERT INTO watchlist (chat_id, symbol, position)
                VALUES (?, ?, ?)
                """,
                (chat_id, symbol, next_position),
            )
        return True

    def remove_from_watchlist(self, chat_id: str, symbol: str) -> bool:
        symbol = symbol.upper()
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM watchlist WHERE chat_id = ? AND symbol = ?",
                (chat_id, symbol),
            )
        return cur.rowcount > 0

    def seed_watchlist_if_empty(self, chat_id: str) -> None:
        """Seed chat_id's watchlist with DEFAULT_WATCHLIST only if it has no rows yet."""
        from agent.db.watchlist import DEFAULT_WATCHLIST

        with self._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM watchlist WHERE chat_id = ?", (chat_id,)
            ).fetchone()["count"]
            if count > 0:
                return
            conn.executemany(
                """
                INSERT INTO watchlist (chat_id, symbol, position)
                VALUES (?, ?, ?)
                """,
                [
                    (chat_id, item["symbol"], position)
                    for position, item in enumerate(DEFAULT_WATCHLIST)
                ],
            )

    def get_active_watchlist_symbols(self) -> list[dict[str, Any]]:
        """Distinct symbols across all active subscribers' watchlists."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT w.symbol AS symbol
                FROM watchlist w
                JOIN subscribers s ON s.chat_id = w.chat_id
                WHERE s.active = 1
                ORDER BY w.symbol
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_earnings_calendar(self, items: list[dict[str, Any]]) -> None:
        updated_at = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                item["symbol"].upper(),
                item["date"],
                item.get("hour"),
                item.get("epsEstimate"),
                item.get("revenueEstimate"),
                updated_at,
            )
            for item in items
            if item.get("symbol") and item.get("date")
        ]
        if not rows:
            return

        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO earnings_calendar (
                    symbol, earning_date, extended_hour, eps_estimate,
                    revenue_estimate, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, earning_date) DO UPDATE SET
                    extended_hour = excluded.extended_hour,
                    eps_estimate = excluded.eps_estimate,
                    revenue_estimate = excluded.revenue_estimate,
                    updated_at = excluded.updated_at
                """,
                rows,
            )

    def delete_earnings_calendar_except(
        self, symbol: str, keep_dates: list[str], date_from: str
    ) -> None:
        """Delete symbol's earnings_calendar rows with earning_date >= date_from
        that are not in keep_dates (stale rows left behind when Finnhub moves
        a company's earnings date to a new date before the old row's TTL
        expires). Rows before date_from are left untouched."""
        symbol = symbol.upper()
        with self._connect() as conn:
            if keep_dates:
                placeholders = ",".join("?" for _ in keep_dates)
                conn.execute(
                    f"""
                    DELETE FROM earnings_calendar
                    WHERE symbol = ? AND earning_date >= ?
                      AND earning_date NOT IN ({placeholders})
                    """,
                    (symbol, date_from, *keep_dates),
                )
            else:
                conn.execute(
                    """
                    DELETE FROM earnings_calendar
                    WHERE symbol = ? AND earning_date >= ?
                    """,
                    (symbol, date_from),
                )

    def get_watchlist_earnings_for_date(self, earning_date: str) -> list[dict[str, Any]]:
        """Earnings on earning_date for every active subscriber's watchlist symbols.

        Joins earnings_calendar ⋈ watchlist ⋈ subscribers (active = 1); each row
        is scoped to the subscriber (chat_id) that should be notified about it.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT w.chat_id AS chat_id, w.symbol AS symbol,
                       e.earning_date, e.extended_hour, e.eps_estimate,
                       e.revenue_estimate, e.updated_at
                FROM watchlist w
                JOIN earnings_calendar e ON e.symbol = w.symbol
                JOIN subscribers s ON s.chat_id = w.chat_id
                WHERE e.earning_date = ? AND s.active = 1
                ORDER BY w.chat_id, w.position
                """,
                (earning_date,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_chat_watchlist_earnings(
        self, chat_id: str, date_from: str, date_to: str
    ) -> list[dict[str, Any]]:
        """Cached earnings in [date_from, date_to] for chat_id's watchlist symbols."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT w.symbol AS symbol,
                       e.earning_date, e.extended_hour, e.eps_estimate,
                       e.revenue_estimate, e.updated_at
                FROM watchlist w
                JOIN earnings_calendar e ON e.symbol = w.symbol
                WHERE w.chat_id = ? AND e.earning_date BETWEEN ? AND ?
                ORDER BY e.earning_date, w.position
                """,
                (chat_id, date_from, date_to),
            ).fetchall()
        return [dict(row) for row in rows]

    def unnotified_symbols(
        self, chat_id: str, symbols: list[str], earning_date: str
    ) -> list[str]:
        if not symbols:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT symbol FROM earnings_notifications
                WHERE chat_id = ? AND earning_date = ?
                  AND symbol IN ({",".join("?" for _ in symbols)})
                """,
                (chat_id, earning_date, *symbols),
            ).fetchall()
        notified = {row["symbol"] for row in rows}
        return [s for s in symbols if s not in notified]

    def mark_notified(
        self, chat_id: str, symbols: list[str], earning_date: str
    ) -> None:
        sent_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO earnings_notifications (
                    chat_id, symbol, earning_date, sent_at
                )
                VALUES (?, ?, ?, ?)
                """,
                [(chat_id, symbol, earning_date, sent_at) for symbol in symbols],
            )

    def summary_notified(self, chat_id: str, summary_date: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM earnings_notifications
                WHERE chat_id = ? AND symbol = '__market_summary__' AND earning_date = ?
                """,
                (chat_id, summary_date),
            ).fetchone()
        return row is not None

    def mark_summary_notified(self, chat_id: str, summary_date: str) -> None:
        sent_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO earnings_notifications (
                    chat_id, symbol, earning_date, sent_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (chat_id, "__market_summary__", summary_date, sent_at),
            )

    def get_company_summary(self, symbol: str, earning_date: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT summary FROM company_summaries
                WHERE symbol = ? AND earning_date = ?
                """,
                (symbol, earning_date),
            ).fetchone()
        return row["summary"] if row else None

    def upsert_company_summary(
        self, symbol: str, earning_date: str, summary: str
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO company_summaries (symbol, earning_date, summary, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol, earning_date) DO UPDATE SET
                    summary = excluded.summary,
                    created_at = excluded.created_at
                """,
                (symbol, earning_date, summary, datetime.now(timezone.utc).isoformat()),
            )

    def insert_news(self, items: list[dict[str, Any]]) -> int:
        """Insert news rows, ignoring duplicate headlines. Returns rows inserted."""
        fetched_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            before = conn.total_changes
            for it in items:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO news (
                        category, symbol, headline, summary, url,
                        published_at, fetched_at, source, high_credibility
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        it["category"],
                        it.get("symbol"),
                        it["headline"],
                        it.get("summary", ""),
                        it.get("url"),
                        it["published_at"],
                        fetched_at,
                        it.get("source"),
                        int(bool(it.get("high_credibility"))),
                    ),
                )
            return conn.total_changes - before

    def list_news_since(self, published_from: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, category, symbol, headline, summary, url, published_at,
                       source, high_credibility
                FROM news
                WHERE published_at >= ?
                ORDER BY published_at DESC
                """,
                (published_from,),
            ).fetchall()
        return [
            {**dict(row), "high_credibility": bool(row["high_credibility"])}
            for row in rows
        ]

    def insert_market_summary(
        self,
        summary: str,
        news_ids: list[int],
        summary_date: str,
        days_back: int,
        news_ids_hash: str,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO market_summaries (
                    created_at, summary, news_ids, summary_date, days_back, news_ids_hash
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    summary,
                    json.dumps(news_ids),
                    summary_date,
                    days_back,
                    news_ids_hash,
                ),
            )
            return cur.lastrowid

    def get_cached_market_summary(
        self, summary_date: str, days_back: int, news_ids_hash: str
    ) -> dict[str, Any] | None:
        """Most recent stored summary matching the cache key, or None."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, summary FROM market_summaries
                WHERE summary_date = ? AND days_back = ? AND news_ids_hash = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (summary_date, days_back, news_ids_hash),
            ).fetchone()
        return dict(row) if row else None

    def claim_update_id(self, update_id: int) -> bool:
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO processed_updates (update_id, ttl) VALUES (?, ?)",
                    (update_id, int(time.time()) + DEDUP_TTL_SECONDS),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def increment_rate_limit(self, chat_id: str, window_key: str, ttl: int) -> int:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO rate_limits (chat_id, window_key, hit_count, notified, ttl)
                VALUES (?, ?, 1, 0, ?)
                ON CONFLICT(chat_id, window_key) DO UPDATE SET
                    hit_count = hit_count + 1
                """,
                (chat_id, window_key, ttl),
            )
            row = conn.execute(
                "SELECT hit_count FROM rate_limits WHERE chat_id = ? AND window_key = ?",
                (chat_id, window_key),
            ).fetchone()
        return row["hit_count"]

    def mark_rate_limit_notified(self, chat_id: str, window_key: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE rate_limits SET notified = 1
                WHERE chat_id = ? AND window_key = ? AND notified = 0
                """,
                (chat_id, window_key),
            )
        return cur.rowcount > 0

    def save_trace(self, trace: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO traces (
                    chat_id, ts, trace_id, user_message, fast_path, playbook_hits,
                    history_msg_count, history_char_count, tools, iterations,
                    usage, usage_by_iteration, total_ms, outcome, reply_char_count,
                    model, ttl
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace["chat_id"],
                    trace["ts"],
                    trace["trace_id"],
                    trace["user_message"],
                    int(trace["fast_path"]),
                    json.dumps(trace["playbook_hits"], ensure_ascii=False),
                    trace["history_msg_count"],
                    trace["history_char_count"],
                    json.dumps(trace["tools"], ensure_ascii=False),
                    trace["iterations"],
                    json.dumps(trace["usage"], ensure_ascii=False),
                    json.dumps(trace["usage_by_iteration"], ensure_ascii=False),
                    trace["total_ms"],
                    trace["outcome"],
                    trace["reply_char_count"],
                    trace.get("model"),
                    trace["ttl"],
                ),
            )

    @staticmethod
    def _row_to_trace(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d.pop("id", None)
        d["fast_path"] = bool(d["fast_path"])
        d["playbook_hits"] = json.loads(d["playbook_hits"])
        d["tools"] = json.loads(d["tools"])
        d["usage"] = json.loads(d["usage"])
        d["usage_by_iteration"] = json.loads(d["usage_by_iteration"])
        return d

    def list_traces(
        self, chat_id: str | None = None, since: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM traces WHERE 1=1"
        params: list[Any] = []
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(chat_id)
        if since is not None:
            query += " AND ts >= ?"
            params.append(since)
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_trace(row) for row in rows]

    def save_correction(
        self,
        chat_id: str,
        correction_text: str,
        context_question: str | None,
        context_answer: str | None,
    ) -> str:
        correction_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO corrections (
                    correction_id, chat_id, created_at, correction_text,
                    context_question, context_answer, status, ttl
                )
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    correction_id,
                    chat_id,
                    datetime.now(timezone.utc).isoformat(),
                    correction_text,
                    context_question,
                    context_answer,
                    int(time.time()) + CORRECTIONS_TTL_SECONDS,
                ),
            )
        return correction_id

    def list_corrections(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM corrections"
        params: list[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def update_correction_status(self, correction_id: str, status: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE corrections SET status = ? WHERE correction_id = ?",
                (status, correction_id),
            )
        return cur.rowcount > 0

    def export_rows(self) -> dict[str, list[dict[str, Any]]]:
        with self._connect() as conn:
            conversations = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT chat_id, timestamp, role, content, ttl
                    FROM conversations
                    ORDER BY id ASC
                    """
                ).fetchall()
            ]
            subscribers = [
                {**dict(row), "active": bool(row["active"])}
                for row in conn.execute(
                    """
                    SELECT chat_id, subscribed_at, active
                    FROM subscribers
                    ORDER BY chat_id
                    """
                ).fetchall()
            ]
            dedup = [
                dict(row)
                for row in conn.execute(
                    "SELECT update_id, ttl FROM processed_updates ORDER BY update_id"
                ).fetchall()
            ]
            watchlist = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT chat_id, symbol, position
                    FROM watchlist
                    ORDER BY chat_id, position
                    """
                ).fetchall()
            ]
            earnings_calendar = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT symbol, earning_date, extended_hour, eps_estimate,
                           revenue_estimate, updated_at
                    FROM earnings_calendar
                    ORDER BY symbol, earning_date
                    """
                ).fetchall()
            ]
            company_summaries = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT symbol, earning_date, summary, created_at
                    FROM company_summaries
                    ORDER BY symbol, earning_date
                    """
                ).fetchall()
            ]
            news = [
                {**dict(row), "high_credibility": bool(row["high_credibility"])}
                for row in conn.execute(
                    """
                    SELECT id, category, symbol, headline, summary, url,
                           published_at, fetched_at, source, high_credibility
                    FROM news
                    ORDER BY id ASC
                    """
                ).fetchall()
            ]
        return {
            "conversations": conversations,
            "subscribers": subscribers,
            "dedup": dedup,
            "watchlist": watchlist,
            "earnings_calendar": earnings_calendar,
            "company_summaries": company_summaries,
            "news": news,
        }


class DynamoDBBackend:
    """DynamoDB-backed StorageBackend implementation for Lambda deployments.

    Table resources are created once in __init__ and reused; singleton
    semantics are provided by get_backend()'s @lru_cache, not by this class.
    """

    def __init__(self) -> None:
        resource = boto3.resource("dynamodb", region_name=aws_region())
        self._conversations = resource.Table(conversations_table())
        self._subscribers = resource.Table(subscribers_table())
        self._processed_updates = resource.Table(processed_updates_table())
        self._watchlist = resource.Table(watchlist_table())
        self._earnings_calendar = resource.Table(earnings_calendar_table())
        self._earnings_notifications = resource.Table(earnings_notifications_table())
        self._company_summaries = resource.Table(company_summaries_table())
        self._news = resource.Table(news_table())
        self._rate_limits = resource.Table(rate_limits_table())
        self._traces = resource.Table(traces_table())
        self._corrections = resource.Table(corrections_table())

    def save_message(self, chat_id: str, role: str, content: str) -> None:
        self._conversations.put_item(
            Item={
                "chat_id": chat_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "role": role,
                "content": content,
                "ttl": int(time.time()) + CONVERSATION_TTL_SECONDS,
            }
        )

    def get_history(self, chat_id: str, limit: int) -> list[dict[str, Any]]:
        resp = self._conversations.query(
            KeyConditionExpression=Key("chat_id").eq(chat_id),
            ScanIndexForward=False,
            Limit=limit,
        )
        items = resp.get("Items", [])
        items.reverse()
        return items

    def subscribe(self, chat_id: str) -> None:
        self._subscribers.put_item(
            Item={
                "chat_id": chat_id,
                "subscribed_at": datetime.now(timezone.utc).isoformat(),
                "active": True,
            }
        )

    def unsubscribe(self, chat_id: str) -> None:
        self._subscribers.update_item(
            Key={"chat_id": chat_id},
            UpdateExpression="SET active = :a",
            ExpressionAttributeValues={":a": False},
        )

    def get_all_active(self) -> list[str]:
        """Scan all subscribers with active=True. Acceptable for small subscriber counts."""
        chat_ids: list[str] = []
        resp = self._subscribers.scan(
            FilterExpression="active = :a",
            ExpressionAttributeValues={":a": True},
        )
        chat_ids.extend(item["chat_id"] for item in resp.get("Items", []))
        while "LastEvaluatedKey" in resp:
            resp = self._subscribers.scan(
                FilterExpression="active = :a",
                ExpressionAttributeValues={":a": True},
                ExclusiveStartKey=resp["LastEvaluatedKey"],
            )
            chat_ids.extend(item["chat_id"] for item in resp.get("Items", []))
        return chat_ids

    def claim_update_id(self, update_id: int) -> bool:
        """Atomically claim an update_id. Returns True if first time, False if duplicate."""
        try:
            self._processed_updates.put_item(
                Item={"update_id": update_id, "ttl": int(time.time()) + DEDUP_TTL_SECONDS},
                ConditionExpression="attribute_not_exists(update_id)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def increment_rate_limit(self, chat_id: str, window_key: str, ttl: int) -> int:
        resp = self._rate_limits.update_item(
            Key={"chat_id": chat_id, "window_key": window_key},
            UpdateExpression="SET #ttl = if_not_exists(#ttl, :ttl) ADD hit_count :incr",
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={":incr": 1, ":ttl": ttl},
            ReturnValues="UPDATED_NEW",
        )
        return int(resp["Attributes"]["hit_count"])

    def mark_rate_limit_notified(self, chat_id: str, window_key: str) -> bool:
        """Atomically flip the notified flag. Returns True only for the caller
        that first sets it, so the "please slow down" reply is sent once per
        window."""
        try:
            self._rate_limits.update_item(
                Key={"chat_id": chat_id, "window_key": window_key},
                UpdateExpression="SET notified = :true",
                ConditionExpression="attribute_not_exists(notified)",
                ExpressionAttributeValues={":true": True},
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def get_watchlist(self, chat_id: str) -> list[dict[str, Any]]:
        """chat_id's own watchlist, sorted by position."""
        resp = self._watchlist.query(KeyConditionExpression=Key("chat_id").eq(chat_id))
        items = sorted(resp.get("Items", []), key=lambda item: item["position"])
        return [{"symbol": item["symbol"]} for item in items]

    def add_to_watchlist(self, chat_id: str, symbol: str) -> bool:
        """Add symbol (uppercased) to chat_id's watchlist. Returns False if already present."""
        symbol = symbol.upper()
        resp = self._watchlist.query(KeyConditionExpression=Key("chat_id").eq(chat_id))
        items = resp.get("Items", [])
        next_position = 0 if not items else max(item["position"] for item in items) + 1
        try:
            self._watchlist.put_item(
                Item={"chat_id": chat_id, "symbol": symbol, "position": next_position},
                ConditionExpression="attribute_not_exists(symbol)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def remove_from_watchlist(self, chat_id: str, symbol: str) -> bool:
        symbol = symbol.upper()
        try:
            self._watchlist.delete_item(
                Key={"chat_id": chat_id, "symbol": symbol},
                ConditionExpression="attribute_exists(symbol)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def seed_watchlist_if_empty(self, chat_id: str) -> None:
        """Seed chat_id's watchlist with DEFAULT_WATCHLIST only if it has no rows yet."""
        from agent.db.watchlist import DEFAULT_WATCHLIST

        resp = self._watchlist.query(
            KeyConditionExpression=Key("chat_id").eq(chat_id), Limit=1
        )
        if resp.get("Items"):
            return
        with self._watchlist.batch_writer() as writer:
            for position, item in enumerate(DEFAULT_WATCHLIST):
                writer.put_item(
                    Item={"chat_id": chat_id, "symbol": item["symbol"], "position": position}
                )

    def upsert_earnings_calendar(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        updated_at = datetime.now(timezone.utc).isoformat()
        ttl = int(time.time()) + EARNINGS_CALENDAR_TTL_SECONDS
        for item in items:
            if not item.get("symbol") or not item.get("date"):
                continue
            record: dict[str, Any] = {
                "symbol": item["symbol"].upper(),
                "earning_date": item["date"],
                "extended_hour": item.get("hour"),
                "updated_at": updated_at,
                "ttl": ttl,
            }
            if item.get("epsEstimate") is not None:
                record["eps_estimate"] = Decimal(str(item["epsEstimate"]))
            if item.get("revenueEstimate") is not None:
                record["revenue_estimate"] = Decimal(str(item["revenueEstimate"]))
            self._earnings_calendar.put_item(Item=record)

    def delete_earnings_calendar_except(
        self, symbol: str, keep_dates: list[str], date_from: str
    ) -> None:
        """Delete symbol's earnings_calendar rows with earning_date >= date_from
        that are not in keep_dates (stale rows left behind when Finnhub moves
        a company's earnings date to a new date before the old row's TTL
        expires). Rows before date_from are left untouched."""
        symbol = symbol.upper()
        keep = set(keep_dates)
        resp = self._earnings_calendar.query(
            KeyConditionExpression=Key("symbol").eq(symbol) & Key("earning_date").gte(date_from)
        )
        items = list(resp.get("Items", []))
        while "LastEvaluatedKey" in resp:
            resp = self._earnings_calendar.query(
                KeyConditionExpression=Key("symbol").eq(symbol)
                & Key("earning_date").gte(date_from),
                ExclusiveStartKey=resp["LastEvaluatedKey"],
            )
            items.extend(resp.get("Items", []))
        for item in items:
            if item["earning_date"] not in keep:
                self._earnings_calendar.delete_item(
                    Key={"symbol": symbol, "earning_date": item["earning_date"]}
                )

    def get_active_watchlist_symbols(self) -> list[dict[str, Any]]:
        """Distinct symbols across all active subscribers' watchlists."""
        symbols: set[str] = set()
        for chat_id in self.get_all_active():
            resp = self._watchlist.query(KeyConditionExpression=Key("chat_id").eq(chat_id))
            symbols.update(item["symbol"] for item in resp.get("Items", []))
        return [{"symbol": symbol} for symbol in sorted(symbols)]

    def get_watchlist_earnings_for_date(self, earning_date: str) -> list[dict[str, Any]]:
        """Earnings on earning_date for every active subscriber's watchlist symbols.

        Application-side join: GSI query for the day's earnings once, then
        per active subscriber query their watchlist and intersect in memory.
        """
        resp = self._earnings_calendar.query(
            IndexName="earning-date-index",
            KeyConditionExpression=Key("earning_date").eq(earning_date),
        )
        earnings_items = list(resp.get("Items", []))
        while "LastEvaluatedKey" in resp:
            resp = self._earnings_calendar.query(
                IndexName="earning-date-index",
                KeyConditionExpression=Key("earning_date").eq(earning_date),
                ExclusiveStartKey=resp["LastEvaluatedKey"],
            )
            earnings_items.extend(resp.get("Items", []))
        earnings_by_symbol = {item["symbol"]: item for item in earnings_items}

        rows: list[dict[str, Any]] = []
        for chat_id in self.get_all_active():
            resp = self._watchlist.query(KeyConditionExpression=Key("chat_id").eq(chat_id))
            watchlist_items = sorted(resp.get("Items", []), key=lambda item: item["position"])
            for wi in watchlist_items:
                earning = earnings_by_symbol.get(wi["symbol"])
                if earning is None:
                    continue
                rows.append(
                    {
                        "chat_id": chat_id,
                        "symbol": wi["symbol"],
                        "earning_date": earning["earning_date"],
                        "extended_hour": earning.get("extended_hour"),
                        "eps_estimate": float(earning["eps_estimate"])
                        if "eps_estimate" in earning
                        else None,
                        "revenue_estimate": float(earning["revenue_estimate"])
                        if "revenue_estimate" in earning
                        else None,
                        "updated_at": earning.get("updated_at"),
                    }
                )
        rows.sort(key=lambda row: row["chat_id"])  # stable: per-chat_id order already by position
        return rows

    def get_chat_watchlist_earnings(
        self, chat_id: str, date_from: str, date_to: str
    ) -> list[dict[str, Any]]:
        """Cached earnings in [date_from, date_to] for chat_id's watchlist symbols."""
        resp = self._watchlist.query(KeyConditionExpression=Key("chat_id").eq(chat_id))
        watchlist_items = sorted(resp.get("Items", []), key=lambda item: item["position"])

        rows: list[dict[str, Any]] = []
        for wi in watchlist_items:
            e_resp = self._earnings_calendar.query(
                KeyConditionExpression=Key("symbol").eq(wi["symbol"])
                & Key("earning_date").between(date_from, date_to)
            )
            for earning in e_resp.get("Items", []):
                rows.append(
                    {
                        "symbol": wi["symbol"],
                        "earning_date": earning["earning_date"],
                        "extended_hour": earning.get("extended_hour"),
                        "eps_estimate": float(earning["eps_estimate"])
                        if "eps_estimate" in earning
                        else None,
                        "revenue_estimate": float(earning["revenue_estimate"])
                        if "revenue_estimate" in earning
                        else None,
                        "updated_at": earning.get("updated_at"),
                        "_position": wi["position"],
                    }
                )
        rows.sort(key=lambda row: (row["earning_date"], row["_position"]))
        for row in rows:
            del row["_position"]
        return rows

    def unnotified_symbols(
        self, chat_id: str, symbols: list[str], earning_date: str
    ) -> list[str]:
        if not symbols:
            return []
        notified: set[str] = set()
        for symbol in symbols:
            resp = self._earnings_notifications.get_item(
                Key={"chat_id": chat_id, "notify_key": f"{symbol}#{earning_date}"}
            )
            if "Item" in resp:
                notified.add(symbol)
        return [symbol for symbol in symbols if symbol not in notified]

    def mark_notified(
        self, chat_id: str, symbols: list[str], earning_date: str
    ) -> None:
        sent_at = datetime.now(timezone.utc).isoformat()
        ttl = int(time.time()) + EARNINGS_NOTIFICATIONS_TTL_SECONDS
        for symbol in symbols:
            try:
                self._earnings_notifications.put_item(
                    Item={
                        "chat_id": chat_id,
                        "notify_key": f"{symbol}#{earning_date}",
                        "symbol": symbol,
                        "earning_date": earning_date,
                        "sent_at": sent_at,
                        "ttl": ttl,
                    },
                    ConditionExpression="attribute_not_exists(notify_key)",
                )
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise

    def summary_notified(self, chat_id: str, summary_date: str) -> bool:
        resp = self._earnings_notifications.get_item(
            Key={"chat_id": chat_id, "notify_key": f"market-summary#{summary_date}"}
        )
        return "Item" in resp

    def mark_summary_notified(self, chat_id: str, summary_date: str) -> None:
        ttl = int(time.time()) + EARNINGS_NOTIFICATIONS_TTL_SECONDS
        try:
            self._earnings_notifications.put_item(
                Item={
                    "chat_id": chat_id,
                    "notify_key": f"market-summary#{summary_date}",
                    "ttl": ttl,
                },
                ConditionExpression="attribute_not_exists(notify_key)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def get_company_summary(self, symbol: str, earning_date: str) -> str | None:
        resp = self._company_summaries.get_item(
            Key={"symbol": symbol, "earning_date": earning_date}
        )
        item = resp.get("Item")
        return item["summary"] if item else None

    def upsert_company_summary(
        self, symbol: str, earning_date: str, summary: str
    ) -> None:
        self._company_summaries.put_item(
            Item={
                "symbol": symbol,
                "earning_date": earning_date,
                "summary": summary,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "ttl": int(time.time()) + COMPANY_SUMMARIES_TTL_SECONDS,
            }
        )

    def insert_news(self, items: list[dict[str, Any]]) -> int:
        """Insert news rows into NewsTable, deduping by headline via a
        conditional put. Returns the count of newly inserted (distinct)
        headlines — duplicates (within this batch or already stored) don't
        count, matching `LocalSQLiteBackend.insert_news`."""
        fetched_at = datetime.now(timezone.utc).isoformat()
        ttl = int(time.time()) + NEWS_TTL_SECONDS
        seen: set[str] = set()
        inserted = 0
        for it in items:
            headline = it["headline"]
            if headline in seen:
                continue
            seen.add(headline)
            item: dict[str, Any] = {
                "headline": headline,
                "bucket": "news",
                "published_at": it["published_at"],
                "category": it["category"],
                "summary": it.get("summary", ""),
                "fetched_at": fetched_at,
                "ttl": ttl,
                "high_credibility": bool(it.get("high_credibility")),
            }
            if it.get("symbol") is not None:
                item["symbol"] = it["symbol"]
            if it.get("url") is not None:
                item["url"] = it["url"]
            if it.get("source") is not None:
                item["source"] = it["source"]
            try:
                self._news.put_item(
                    Item=item, ConditionExpression="attribute_not_exists(headline)"
                )
                inserted += 1
            except ClientError as e:
                if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
        return inserted

    def list_news_since(self, published_from: str) -> list[dict[str, Any]]:
        """News published on/after published_from, newest first.

        `id` on each returned row is the headline (the NewsTable PK), so
        `market_summary.generate_market_summary`'s `it["id"]` usage works
        unchanged across both backends.
        """
        resp = self._news.query(
            IndexName="published-at-index",
            KeyConditionExpression=Key("bucket").eq("news")
            & Key("published_at").gte(published_from),
            ScanIndexForward=False,
        )
        items = list(resp.get("Items", []))
        while "LastEvaluatedKey" in resp:
            resp = self._news.query(
                IndexName="published-at-index",
                KeyConditionExpression=Key("bucket").eq("news")
                & Key("published_at").gte(published_from),
                ScanIndexForward=False,
                ExclusiveStartKey=resp["LastEvaluatedKey"],
            )
            items.extend(resp.get("Items", []))
        return [
            {
                "id": item["headline"],
                "category": item.get("category"),
                "symbol": item.get("symbol"),
                "headline": item["headline"],
                "summary": item.get("summary"),
                "url": item.get("url"),
                "published_at": item["published_at"],
                "source": item.get("source"),
                "high_credibility": bool(item.get("high_credibility")),
            }
            for item in items
        ]

    def insert_market_summary(
        self,
        summary: str,
        news_ids: list,
        summary_date: str,
        days_back: int,
        news_ids_hash: str,
    ) -> None:
        """Persist a Groq-generated summary as a Groq-call cache entry, keyed
        by (summary_date, days_back, news_ids_hash) so a repeat call with the
        same inputs can skip the Groq call (ADR-0006 §3 revision,
        2026-07-26). Stored in NewsTable under a distinct
        `bucket="market_summary"` / `headline`-PK cache key so no new table
        is needed; a short TTL is enough since it only needs to survive
        same-day cache hits plus slack for cross-midnight retries."""
        self._news.put_item(
            Item={
                "headline": f"SUMMARY#{summary_date}#{days_back}#{news_ids_hash}",
                "bucket": "market_summary",
                "summary": summary,
                "news_ids": json.dumps(news_ids),
                "summary_date": summary_date,
                "days_back": days_back,
                "news_ids_hash": news_ids_hash,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "ttl": int(time.time()) + MARKET_SUMMARY_TTL_SECONDS,
            }
        )

    def get_cached_market_summary(
        self, summary_date: str, days_back: int, news_ids_hash: str
    ) -> dict[str, Any] | None:
        resp = self._news.get_item(
            Key={"headline": f"SUMMARY#{summary_date}#{days_back}#{news_ids_hash}"}
        )
        item = resp.get("Item")
        if not item:
            return None
        return {"id": item["headline"], "summary": item["summary"]}

    def save_trace(self, trace: dict[str, Any]) -> None:
        # Nested/compound fields are stored as JSON strings (rather than
        # DynamoDB maps/lists) to sidestep float->Decimal conversion for
        # playbook scores and keep list_traces' return shape identical to
        # LocalSQLiteBackend's (both json.loads on read).
        self._traces.put_item(
            Item={
                "chat_id": trace["chat_id"],
                "sk": f"{trace['ts']}#{trace['trace_id']}",
                "ts": trace["ts"],
                "trace_id": trace["trace_id"],
                "user_message": trace["user_message"],
                "fast_path": bool(trace["fast_path"]),
                "playbook_hits": json.dumps(trace["playbook_hits"], ensure_ascii=False),
                "history_msg_count": trace["history_msg_count"],
                "history_char_count": trace["history_char_count"],
                "tools": json.dumps(trace["tools"], ensure_ascii=False),
                "iterations": trace["iterations"],
                "usage": json.dumps(trace["usage"], ensure_ascii=False),
                "usage_by_iteration": json.dumps(trace["usage_by_iteration"], ensure_ascii=False),
                "total_ms": trace["total_ms"],
                "outcome": trace["outcome"],
                "reply_char_count": trace["reply_char_count"],
                "model": trace.get("model"),
                "ttl": trace["ttl"],
                "bucket": trace.get("bucket", "trace"),
            }
        )

    @staticmethod
    def _item_to_trace(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "chat_id": item["chat_id"],
            "ts": item["ts"],
            "trace_id": item["trace_id"],
            "user_message": item.get("user_message"),
            "fast_path": bool(item.get("fast_path")),
            "playbook_hits": json.loads(item["playbook_hits"]),
            "history_msg_count": int(item.get("history_msg_count", 0)),
            "history_char_count": int(item.get("history_char_count", 0)),
            "tools": json.loads(item["tools"]),
            "iterations": int(item.get("iterations", 0)),
            "usage": json.loads(item["usage"]),
            "usage_by_iteration": json.loads(item["usage_by_iteration"]),
            "total_ms": int(item.get("total_ms", 0)),
            "outcome": item.get("outcome"),
            "reply_char_count": int(item.get("reply_char_count", 0)),
            "model": item.get("model"),
            "ttl": int(item.get("ttl", 0)),
        }

    def list_traces(
        self, chat_id: str | None = None, since: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if chat_id is not None:
            key_cond = Key("chat_id").eq(chat_id)
            if since is not None:
                key_cond &= Key("sk").gte(since)
            resp = self._traces.query(
                KeyConditionExpression=key_cond,
                ScanIndexForward=False,
                Limit=limit,
            )
        else:
            key_cond = Key("bucket").eq("trace")
            if since is not None:
                key_cond &= Key("ts").gte(since)
            resp = self._traces.query(
                IndexName="trace-time-index",
                KeyConditionExpression=key_cond,
                ScanIndexForward=False,
                Limit=limit,
            )
        return [self._item_to_trace(item) for item in resp.get("Items", [])]

    def save_correction(
        self,
        chat_id: str,
        correction_text: str,
        context_question: str | None,
        context_answer: str | None,
    ) -> str:
        correction_id = str(uuid.uuid4())
        self._corrections.put_item(
            Item={
                "correction_id": correction_id,
                "chat_id": chat_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "correction_text": correction_text,
                "context_question": context_question,
                "context_answer": context_answer,
                "status": "pending",
                "ttl": int(time.time()) + CORRECTIONS_TTL_SECONDS,
            }
        )
        return correction_id

    def list_corrections(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if status is not None:
            resp = self._corrections.query(
                IndexName="status-index",
                KeyConditionExpression=Key("status").eq(status),
                ScanIndexForward=False,
                Limit=limit,
            )
            return [dict(item) for item in resp.get("Items", [])]

        items: list[dict[str, Any]] = []
        resp = self._corrections.scan()
        items.extend(resp.get("Items", []))
        while "LastEvaluatedKey" in resp:
            resp = self._corrections.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
            items.extend(resp.get("Items", []))
        items.sort(key=lambda it: it["created_at"], reverse=True)
        return [dict(item) for item in items[:limit]]

    def update_correction_status(self, correction_id: str, status: str) -> bool:
        try:
            self._corrections.update_item(
                Key={"correction_id": correction_id},
                UpdateExpression="SET #s = :s",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":s": status},
                ConditionExpression="attribute_exists(correction_id)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
