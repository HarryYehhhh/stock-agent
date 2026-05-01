import json
from types import SimpleNamespace

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
