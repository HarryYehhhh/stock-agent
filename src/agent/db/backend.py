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
