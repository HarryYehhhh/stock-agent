import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent import processor_handler, scheduler_handler
from agent.webhook_handler import handler as webhook_handler


CTX = SimpleNamespace(
    function_name="test",
    memory_limit_in_mb=128,
    invoked_function_arn="arn:aws:lambda:test",
    aws_request_id="req-1",
)


def _event(body: dict) -> dict:
    return {
        "body": json.dumps(body),
        "headers": {"x-telegram-bot-api-secret-token": "fake-secret"},
    }


def test_local_webhook_duplicate_update_enqueues_once(aws, local_store):
    event = _event({"update_id": 300, "message": {"chat": {"id": 1}, "text": "hi"}})

    assert webhook_handler(event, CTX)["statusCode"] == 200
    assert webhook_handler(event, CTX)["statusCode"] == 200

    msgs = aws["sqs"].receive_message(QueueUrl=aws["queue_url"], MaxNumberOfMessages=10)
    assert len(msgs.get("Messages", [])) == 1

    with sqlite3.connect(local_store) as conn:
        rows = conn.execute("SELECT update_id FROM processed_updates").fetchall()
    assert rows == [(300,)]


def test_processor_subscribe_and_scheduler_share_local_store(local_store, monkeypatch):
    fake_send = MagicMock()
    monkeypatch.setattr(processor_handler, "send_message", fake_send)
    monkeypatch.setattr(scheduler_handler, "send_message", fake_send)
    monkeypatch.setattr(scheduler_handler, "get_earnings_calendar", lambda *_: [{"symbol": "AAPL"}])
    monkeypatch.setattr(scheduler_handler, "get_company_industry", lambda _: "Technology")
    monkeypatch.setattr(scheduler_handler, "run_agent", lambda *_: "summary")

    processor_handler._process_update({"message": {"chat": {"id": 777}, "text": "/subscribe"}})
    assert scheduler_handler.handler({}, CTX) == {"statusCode": 200}

    assert fake_send.call_args_list[-1].args[0] == "777"
    assert "summary" in fake_send.call_args_list[-1].args[1]
