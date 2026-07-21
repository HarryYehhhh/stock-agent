"""Storage backend selection and local sqlite implementation."""

from __future__ import annotations

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist (
                    ticker TEXT PRIMARY KEY,
                    exchange TEXT NOT NULL,
                    position INTEGER NOT NULL
                )
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
                CREATE TABLE IF NOT EXISTS earnings_notifications (
                    chat_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    earning_date TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY (chat_id, symbol, earning_date)
                )
                """
            )
            self._seed_watchlist(conn)

    def _seed_watchlist(self, conn: sqlite3.Connection) -> None:
        from agent.db.watchlist import DEFAULT_WATCHLIST

        rows = [
            (item["ticker"], item["exchange"], position)
            for position, item in enumerate(DEFAULT_WATCHLIST)
        ]
        conn.executemany(
            """
            INSERT INTO watchlist (ticker, exchange, position)
            VALUES (?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                exchange = excluded.exchange,
                position = excluded.position
            """,
            rows,
        )
        conn.execute(
            f"""
            DELETE FROM watchlist
            WHERE ticker NOT IN ({",".join("?" for _ in rows)})
            """,
            [ticker for ticker, _, _ in rows],
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

    def get_watchlist(self) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT exchange, ticker
                FROM watchlist
                ORDER BY position
                """
            ).fetchall()
        return [{"exchange": row["exchange"], "ticker": row["ticker"]} for row in rows]

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
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT w.ticker AS symbol, w.exchange, e.earning_date,
                       e.extended_hour, e.eps_estimate, e.revenue_estimate,
                       e.updated_at
                FROM watchlist w
                JOIN earnings_calendar e ON e.symbol = w.ticker
                WHERE e.earning_date = ?
                ORDER BY w.position
                """,
                (earning_date,),
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
