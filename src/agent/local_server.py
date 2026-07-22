"""FastAPI shim for local development.

Exposes /webhook as a HTTP endpoint that delegates to webhook_handler.handler,
then synchronously runs processor_handler against the in-memory SQS payload so
ngrok-driven local testing exercises the full flow without real SQS.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from json import JSONDecodeError

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response

from agent.db.backend import get_backend
from agent.market_summary import generate_market_summary, list_stored_news, refresh_news
from agent.processor_handler import _process_update
from agent.tools.earnings import get_earnings_calendar
from agent.webhook_handler import handler as webhook_handler

logging.basicConfig(level=logging.INFO)
app = FastAPI()


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    raw = await request.body()
    event = {
        "body": raw.decode("utf-8"),
        "headers": {k: v for k, v in request.headers.items()},
    }
    result = webhook_handler(event, None)
    if result.get("statusCode") == 200 and raw:
        try:
            _process_update(json.loads(raw))
        except Exception:
            logging.exception("local processor failed")
    return Response(status_code=result.get("statusCode", 200))


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/market-summary")
async def market_summary(days_back: int = Query(1, ge=1, le=7)) -> dict:
    try:
        stored = refresh_news(days_back)
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Finnhub upstream timeout") from exc
    result = generate_market_summary(days_back)
    result["stored_news"] = stored
    return result


@app.get("/news")
async def news(days_back: int = Query(1, ge=1, le=7)) -> dict:
    items = list_stored_news(days_back)
    return {"count": len(items), "news": items}


@app.get("/watchlist/{chat_id}")
async def watchlist(chat_id: str) -> dict:
    items = get_backend().get_watchlist(chat_id)
    return {"chat_id": chat_id, "count": len(items), "watchlist": items}


@app.get("/watchlist/{chat_id}/earnings")
async def watchlist_earnings(
    chat_id: str, days: int = Query(14, ge=1, le=90)
) -> dict:
    today = date.today()
    items = get_backend().get_chat_watchlist_earnings(
        chat_id, today.isoformat(), (today + timedelta(days=days)).isoformat()
    )
    return {"chat_id": chat_id, "days": days, "count": len(items), "earnings": items}


@app.get("/earnings")
async def earnings(
    date_from: date = Query(...),
    date_to: date = Query(...),
    symbol: str | None = None,
) -> dict:
    if date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must be before or equal to date_to",
        )

    try:
        items = get_earnings_calendar(
            date_from.isoformat(),
            date_to.isoformat(),
            symbol=symbol,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Finnhub upstream timeout") from exc
    except (httpx.HTTPStatusError, JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="Finnhub upstream error") from exc

    return {"earningsCalendar": items}
