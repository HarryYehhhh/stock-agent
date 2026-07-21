"""Fixed earnings watchlist."""

from __future__ import annotations

from agent.db.backend import get_backend, selected_backend_name

DEFAULT_WATCHLIST = [
    {"exchange": "NASDAQGM", "ticker": "AAOI"},
    {"exchange": "NASDAQGS", "ticker": "AAPL"},
    {"exchange": "NASDAQGS", "ticker": "AMD"},
    {"exchange": "NASDAQGS", "ticker": "AMZN"},
    {"exchange": "NASDAQGS", "ticker": "ARM"},
    {"exchange": "NASDAQGS", "ticker": "ASML"},
    {"exchange": "NASDAQGS", "ticker": "AXTI"},
    {"exchange": "NYSE", "ticker": "COHR"},
    {"exchange": "NYSE", "ticker": "CRCL"},
    {"exchange": "NASDAQGS", "ticker": "CRWD"},
    {"exchange": "NYSE", "ticker": "DELL"},
    {"exchange": "NYSE", "ticker": "GLW"},
    {"exchange": "NASDAQGS", "ticker": "GOOGL"},
    {"exchange": "NASDAQGS", "ticker": "INTC"},
    {"exchange": "NASDAQGS", "ticker": "LITE"},
    {"exchange": "NASDAQGS", "ticker": "META"},
    {"exchange": "NASDAQGS", "ticker": "MRVL"},
    {"exchange": "NASDAQGS", "ticker": "MSFT"},
    {"exchange": "NASDAQGS", "ticker": "MU"},
    {"exchange": "NYSE", "ticker": "NET"},
    {"exchange": "NASDAQGS", "ticker": "NVDA"},
    {"exchange": "NASDAQGM", "ticker": "QQQ"},
    {"exchange": "NASDAQGS", "ticker": "SNDK"},
    {"exchange": "NASDAQGS", "ticker": "SOFI"},
    {"exchange": "NASDAQGM", "ticker": "SOXX"},
    {"exchange": "NASDAQGS", "ticker": "SPCX"},
    {"exchange": "NASDAQGS", "ticker": "TSLA"},
    {"exchange": "NYSE", "ticker": "TSM"},
]


def get_watchlist() -> list[dict[str, str]]:
    if selected_backend_name() == "local":
        return get_backend().get_watchlist()
    return [item.copy() for item in DEFAULT_WATCHLIST]
