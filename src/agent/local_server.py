"""FastAPI shim for local development.

Exposes /webhook as a HTTP endpoint that delegates to webhook_handler.handler,
then synchronously runs processor_handler against the in-memory SQS payload so
ngrok-driven local testing exercises the full flow without real SQS.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from json import JSONDecodeError

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response

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
