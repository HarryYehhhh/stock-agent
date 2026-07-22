"""Per-subscriber earnings watchlist (local backend); global default elsewhere."""

from __future__ import annotations

from agent.db.backend import get_backend, selected_backend_name

DEFAULT_WATCHLIST = [
    {"exchange": "NASDAQGM", "symbol": "AAOI"},
    {"exchange": "NASDAQGS", "symbol": "AAPL"},
    {"exchange": "NASDAQGS", "symbol": "AMD"},
    {"exchange": "NASDAQGS", "symbol": "AMZN"},
    {"exchange": "NASDAQGS", "symbol": "ARM"},
    {"exchange": "NASDAQGS", "symbol": "ASML"},
    {"exchange": "NASDAQGS", "symbol": "AXTI"},
    {"exchange": "NYSE", "symbol": "COHR"},
    {"exchange": "NYSE", "symbol": "CRCL"},
    {"exchange": "NASDAQGS", "symbol": "CRWD"},
    {"exchange": "NYSE", "symbol": "DELL"},
    {"exchange": "NYSE", "symbol": "GLW"},
    {"exchange": "NASDAQGS", "symbol": "GOOGL"},
    {"exchange": "NASDAQGS", "symbol": "INTC"},
    {"exchange": "NASDAQGS", "symbol": "LITE"},
    {"exchange": "NASDAQGS", "symbol": "META"},
    {"exchange": "NASDAQGS", "symbol": "MRVL"},
    {"exchange": "NASDAQGS", "symbol": "MSFT"},
    {"exchange": "NASDAQGS", "symbol": "MU"},
    {"exchange": "NYSE", "symbol": "NET"},
    {"exchange": "NASDAQGS", "symbol": "NVDA"},
    {"exchange": "NASDAQGM", "symbol": "QQQ"},
    {"exchange": "NASDAQGS", "symbol": "SNDK"},
    {"exchange": "NASDAQGS", "symbol": "SOFI"},
    {"exchange": "NASDAQGM", "symbol": "SOXX"},
    {"exchange": "NASDAQGS", "symbol": "SPCX"},
    {"exchange": "NASDAQGS", "symbol": "TSLA"},
    {"exchange": "NYSE", "symbol": "TSM"},
]


def get_watchlist(chat_id: str | None = None) -> list[dict[str, str]]:
    """Local backend: chat_id's own watchlist. DynamoDB: global DEFAULT_WATCHLIST."""
    if selected_backend_name() == "local":
        return get_backend().get_watchlist(chat_id)
    return [item.copy() for item in DEFAULT_WATCHLIST]


def add_to_watchlist(chat_id: str, symbol: str) -> bool:
    """Add symbol to chat_id's watchlist. Returns True if newly added.

    Raises NotImplementedError on the dynamodb backend (local-only feature).
    """
    if selected_backend_name() == "local":
        return get_backend().add_to_watchlist(chat_id, symbol)
    raise NotImplementedError("watchlist management is only supported on the local backend")


def remove_from_watchlist(chat_id: str, symbol: str) -> bool:
    """Remove symbol from chat_id's watchlist. Returns True if actually removed.

    Raises NotImplementedError on the dynamodb backend (local-only feature).
    """
    if selected_backend_name() == "local":
        return get_backend().remove_from_watchlist(chat_id, symbol)
    raise NotImplementedError("watchlist management is only supported on the local backend")


def seed_watchlist_if_empty(chat_id: str) -> None:
    """Seed chat_id with DEFAULT_WATCHLIST if it has no watchlist rows yet.

    No-op on the dynamodb backend, which already returns DEFAULT_WATCHLIST globally.
    """
    if selected_backend_name() == "local":
        get_backend().seed_watchlist_if_empty(chat_id)
