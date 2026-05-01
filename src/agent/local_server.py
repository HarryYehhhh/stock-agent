"""FastAPI shim for local development.

Exposes /webhook as a HTTP endpoint that delegates to webhook_handler.handler,
then synchronously runs processor_handler against the in-memory SQS payload so
ngrok-driven local testing exercises the full flow without real SQS.
"""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI, Request, Response

from agent.processor_handler import _process_update
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
