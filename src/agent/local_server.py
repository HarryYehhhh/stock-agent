"""FastAPI shim for local development.

Exposes /webhook as a HTTP endpoint that delegates to webhook_handler.handler,
then synchronously runs processor_handler against the in-memory SQS payload so
ngrok-driven local testing exercises the full flow without real SQS.
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import date, timedelta
from json import JSONDecodeError
from types import SimpleNamespace

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response

from agent import webhook_handler as webhook_handler_module
from agent.db.backend import get_backend
from agent.market_summary import generate_market_summary, list_stored_news, refresh_news
from agent.processor_handler import _process_update
from agent.tools.earnings import get_earnings_calendar
from agent.webhook_handler import handler as webhook_handler

logging.basicConfig(level=logging.INFO)
app = FastAPI()

# aws_lambda_powertools' @logger.inject_lambda_context reads these attributes off
# `context` on every call, so local dev needs a stand-in for the real Lambda context.
_LOCAL_LAMBDA_CONTEXT = SimpleNamespace(
    function_name="local-webhook",
    memory_limit_in_mb=128,
    invoked_function_arn="arn:aws:lambda:local:0:function:local-webhook",
    aws_request_id="local",
)


@contextlib.contextmanager
def _no_op_sqs():
    """webhook_handler.handler() enqueues to real SQS for the Lambda path, but
    local dev has no queue provisioned and immediately runs _process_update
    synchronously below anyway, so the enqueue is both unnecessary and fatal
    locally (QueueDoesNotExist). Swap in a no-op client only for the duration
    of this call so webhook_handler.py itself — and its Lambda behaviour —
    stays unchanged.
    """
    original = webhook_handler_module._sqs
    webhook_handler_module._sqs = lambda: SimpleNamespace(send_message=lambda **_: None)
    try:
        yield
    finally:
        webhook_handler_module._sqs = original


@app.post("/webhook")
async def webhook(request: Request) -> Response:
    raw = await request.body()
    event = {
        "body": raw.decode("utf-8"),
        "headers": {k: v for k, v in request.headers.items()},
    }
    with _no_op_sqs():
        result = webhook_handler(event, _LOCAL_LAMBDA_CONTEXT)
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
