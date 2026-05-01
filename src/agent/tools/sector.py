"""Sector → symbol list lookup, backed by config/sector_symbols.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SECTOR_TOOL = {
    "name": "get_stocks_by_sector",
    "description": (
        "Return the list of US stock tickers in a given sector. "
        "Supported sectors: Technology, Healthcare, Finance, Energy, Consumer. "
        "If the sector is not recognised, the tool returns the available sector names."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sector": {
                "type": "string",
                "description": "Sector name, e.g. Technology, Healthcare",
            },
        },
        "required": ["sector"],
    },
}


def _config_path() -> Path:
    # Lambda container copies config/ next to the code; locally it lives at repo root.
    candidates = [
        Path(__file__).resolve().parents[3] / "config" / "sector_symbols.json",
        Path("/var/task/config/sector_symbols.json"),
        Path.cwd() / "config" / "sector_symbols.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError("sector_symbols.json not found in any expected location")


@lru_cache(maxsize=1)
def _sectors() -> dict[str, list[str]]:
    return json.loads(_config_path().read_text())


def get_stocks_by_sector(sector: str) -> dict:
    sectors = _sectors()
    if sector not in sectors:
        return {
            "error": f"unknown sector '{sector}'",
            "available_sectors": list(sectors.keys()),
        }
    return {"sector": sector, "symbols": sectors[sector]}
