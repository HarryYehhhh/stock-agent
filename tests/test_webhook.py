import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent import webhook_handler as webhook_handler_module
from agent.db.rate_limit import MINUTE_LIMIT
from agent.webhook_handler import handler

CTX = SimpleNamespace(
    function_name="test",
    memory_limit_in_mb=128,
    invoked_function_arn="arn:aws:lambda:test",
    aws_request_id="req-1",
)


def _event(body: dict, secret: str | None = "fake-secret") -> dict:
    headers = {}
    if secret is not None:
        headers["x-telegram-bot-api-secret-token"] = secret
    return {"body": json.dumps(body), "headers": headers}


def test_rejects_missing_secret(aws):
    resp = handler(_event({"update_id": 1}, secret=None), CTX)
    assert resp["statusCode"] == 401


def test_rejects_wrong_secret(aws):
    resp = handler(_event({"update_id": 1}, secret="bad"), CTX)
    assert resp["statusCode"] == 401


def test_valid_request_enqueues_to_sqs(aws):
    resp = handler(_event({"update_id": 100, "message": {"chat": {"id": 1}, "text": "hi"}}), CTX)
    assert resp["statusCode"] == 200

    msgs = aws["sqs"].receive_message(QueueUrl=aws["queue_url"], MaxNumberOfMessages=10)
    assert len(msgs.get("Messages", [])) == 1
    assert json.loads(msgs["Messages"][0]["Body"])["update_id"] == 100


def test_duplicate_update_id_does_not_enqueue(aws):
    event = _event({"update_id": 200, "message": {"chat": {"id": 1}, "text": "hi"}})

    assert handler(event, CTX)["statusCode"] == 200
    assert handler(event, CTX)["statusCode"] == 200  # second call, dedup hit

    msgs = aws["sqs"].receive_message(QueueUrl=aws["queue_url"], MaxNumberOfMessages=10)
    assert len(msgs.get("Messages", [])) == 1


def test_missing_update_id_returns_200_silently(aws):
    resp = handler(_event({"message": {"chat": {"id": 1}}}), CTX)
    assert resp["statusCode"] == 200


def test_rate_limited_message_is_not_enqueued_and_notifies_once(aws, monkeypatch):
    fake_send = MagicMock()
    monkeypatch.setattr(webhook_handler_module, "send_plain_message", fake_send)

    for i in range(MINUTE_LIMIT):
        resp = handler(
            _event({"update_id": 900 + i, "message": {"chat": {"id": 9}, "text": "hi"}}), CTX
        )
        assert resp["statusCode"] == 200

    resp = handler(_event({"update_id": 910, "message": {"chat": {"id": 9}, "text": "hi"}}), CTX)
    assert resp["statusCode"] == 200
    fake_send.assert_called_once()
    assert fake_send.call_args.args[0] == "9"

    msgs = aws["sqs"].receive_message(QueueUrl=aws["queue_url"], MaxNumberOfMessages=10)
    assert len(msgs.get("Messages", [])) == MINUTE_LIMIT  # the over-limit update wasn't enqueued

    # A second over-limit update in the same minute is silently dropped: no repeat notice.
    resp = handler(_event({"update_id": 911, "message": {"chat": {"id": 9}, "text": "hi"}}), CTX)
    assert resp["statusCode"] == 200
    fake_send.assert_called_once()


def test_rate_limit_check_failure_fails_open_and_still_enqueues(aws, monkeypatch):
    """Rate limiting is a protective guard, not core functionality — if the
    check itself blows up (e.g. table outage), the webhook must still
    process the message instead of taking down the whole pipeline."""
    monkeypatch.setattr(
        webhook_handler_module,
        "check_rate_limit",
        MagicMock(side_effect=RuntimeError("table unavailable")),
    )

    resp = handler(_event({"update_id": 920, "message": {"chat": {"id": 9}, "text": "hi"}}), CTX)

    assert resp["statusCode"] == 200
    msgs = aws["sqs"].receive_message(QueueUrl=aws["queue_url"], MaxNumberOfMessages=10)
    assert len(msgs.get("Messages", [])) == 1
