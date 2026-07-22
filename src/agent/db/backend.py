"""Storage backend selection and local sqlite implementation."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

CONVERSATION_TTL_SECONDS = 30 * 24 * 3600
DEDUP_TTL_SECONDS = 3600
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


@lru_cache(maxsize=1)
def get_backend() -> "LocalSQLiteBackend":
    if selected_backend_name() != "local":
        raise ValueError("get_backend() is only available for local storage")
    return LocalSQLiteBackend(local_store_path())


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
                    exchange TEXT,
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
                    fetched_at TEXT NOT NULL
                )
                """
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

    def _migrate_watchlist_schema(self, conn: sqlite3.Connection) -> None:
        """One-time migration from the old global watchlist to per-subscriber rows.

        Old shape: watchlist(ticker PRIMARY KEY, exchange, position) — a single
        global list. New shape: watchlist(chat_id, symbol, exchange, position),
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

        old_rows = conn.execute(
            "SELECT ticker, exchange, position FROM watchlist"
        ).fetchall()
        subscriber_ids = [
            row["chat_id"] for row in conn.execute("SELECT chat_id FROM subscribers")
        ]

        conn.execute("DROP TABLE watchlist")
        conn.execute(
            """
            CREATE TABLE watchlist (
                chat_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT,
                position INTEGER NOT NULL,
                PRIMARY KEY (chat_id, symbol)
            )
            """
        )

        if subscriber_ids and old_rows:
            conn.executemany(
                """
                INSERT INTO watchlist (chat_id, symbol, exchange, position)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (chat_id, row["ticker"], row["exchange"], row["position"])
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
                SELECT symbol, exchange
                FROM watchlist
                WHERE chat_id = ?
                ORDER BY position
                """,
                (chat_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_to_watchlist(
        self, chat_id: str, symbol: str, exchange: str | None = None
    ) -> bool:
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
                INSERT INTO watchlist (chat_id, symbol, exchange, position)
                VALUES (?, ?, ?, ?)
                """,
                (chat_id, symbol, exchange, next_position),
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
                INSERT INTO watchlist (chat_id, symbol, exchange, position)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (chat_id, item["symbol"], item["exchange"], position)
                    for position, item in enumerate(DEFAULT_WATCHLIST)
                ],
            )

    def get_active_watchlist_symbols(self) -> list[dict[str, Any]]:
        """Distinct symbols (with exchange) across all active subscribers' watchlists."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT w.symbol AS symbol, w.exchange AS exchange
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

    def get_watchlist_earnings_for_date(self, earning_date: str) -> list[dict[str, Any]]:
        """Earnings on earning_date for every active subscriber's watchlist symbols.

        Joins earnings_calendar ⋈ watchlist ⋈ subscribers (active = 1); each row
        is scoped to the subscriber (chat_id) that should be notified about it.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT w.chat_id AS chat_id, w.symbol AS symbol, w.exchange AS exchange,
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
                SELECT w.symbol AS symbol, w.exchange AS exchange,
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
        inserted = 0
        with self._connect() as conn:
            for it in items:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO news (
                        category, symbol, headline, summary, url,
                        published_at, fetched_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        it["category"],
                        it.get("symbol"),
                        it["headline"],
                        it.get("summary", ""),
                        it.get("url"),
                        it["published_at"],
                        fetched_at,
                    ),
                )
                inserted += cur.rowcount
        return inserted

    def list_news_since(self, published_from: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, category, symbol, headline, summary, url, published_at
                FROM news
                WHERE published_at >= ?
                ORDER BY published_at DESC
                """,
                (published_from,),
            ).fetchall()
        return [dict(row) for row in rows]

    def insert_market_summary(self, summary: str, news_ids: list[int]) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO market_summaries (created_at, summary, news_ids)
                VALUES (?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    summary,
                    json.dumps(news_ids),
                ),
            )
            return cur.lastrowid

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
        return {
            "conversations": conversations,
            "subscribers": subscribers,
            "dedup": dedup,
        }
