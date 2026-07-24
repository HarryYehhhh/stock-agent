"""Per-subscriber earnings watchlist (local backend); global default elsewhere."""

from __future__ import annotations

from agent.db.backend import get_backend

DEFAULT_WATCHLIST = [
    {"symbol": "AAOI"},
    {"symbol": "AAPL"},
    {"symbol": "AMD"},
    {"symbol": "AMZN"},
    {"symbol": "ARM"},
    {"symbol": "ASML"},
    {"symbol": "AXTI"},
    {"symbol": "COHR"},
    {"symbol": "CRCL"},
    {"symbol": "CRWD"},
    {"symbol": "DELL"},
    {"symbol": "GLW"},
    {"symbol": "GOOGL"},
    {"symbol": "INTC"},
    {"symbol": "LITE"},
    {"symbol": "META"},
    {"symbol": "MRVL"},
    {"symbol": "MSFT"},
    {"symbol": "MU"},
    {"symbol": "NET"},
    {"symbol": "NVDA"},
    {"symbol": "QQQ"},
    {"symbol": "SNDK"},
    {"symbol": "SOFI"},
    {"symbol": "SOXX"},
    {"symbol": "SPCX"},
    {"symbol": "TSLA"},
    {"symbol": "TSM"},
]


def get_watchlist(chat_id: str | None = None) -> list[dict[str, str]]:
    """Local backend: chat_id's own watchlist. DynamoDB: global DEFAULT_WATCHLIST."""
    return get_backend().get_watchlist(chat_id)


def add_to_watchlist(chat_id: str, symbol: str) -> bool:
    """Add symbol to chat_id's watchlist. Returns True if newly added.

    Raises NotImplementedError on the dynamodb backend (local-only feature).
    """
    return get_backend().add_to_watchlist(chat_id, symbol)


def remove_from_watchlist(chat_id: str, symbol: str) -> bool:
    """Remove symbol from chat_id's watchlist. Returns True if actually removed.

    Raises NotImplementedError on the dynamodb backend (local-only feature).
    """
    return get_backend().remove_from_watchlist(chat_id, symbol)


def seed_watchlist_if_empty(chat_id: str) -> None:
    """Seed chat_id with DEFAULT_WATCHLIST if it has no watchlist rows yet.

    No-op on the dynamodb backend, which already returns DEFAULT_WATCHLIST globally.
    """
    get_backend().seed_watchlist_if_empty(chat_id)
