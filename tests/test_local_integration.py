import json
import sqlite3
import threading
import time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import local_server, market_summary, processor_handler, scheduler_handler
from agent.db.backend import get_backend
from agent.local_server import app
from agent.tools import _http as tools_http
from agent.tools import earnings
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
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    def fake_earnings(*_, symbol):
        # Mimics the real tool: persists fetched rows into the local store.
        items = [{"symbol": symbol, "date": tomorrow}] if symbol == "AAPL" else []
        get_backend().upsert_earnings_calendar(items)
        return items

    monkeypatch.setattr(scheduler_handler, "get_earnings_calendar", fake_earnings)
    monkeypatch.setattr(scheduler_handler, "run_agent", lambda *_: "summary")

    processor_handler._process_update({"message": {"chat": {"id": 777}, "text": "/subscribe"}})
    assert scheduler_handler.sync_earnings() == []
    assert scheduler_handler.handler({}, CTX) == {"statusCode": 200}

    assert fake_send.call_args_list[-1].args[0] == "777"
    assert "summary" in fake_send.call_args_list[-1].args[1]


def test_rest_watchlist_returns_user_watchlist(local_store):
    get_backend().add_to_watchlist("111", "AAPL")
    get_backend().add_to_watchlist("111", "NVDA")
    get_backend().add_to_watchlist("222", "TSM")

    response = TestClient(app).get("/watchlist/111")

    assert response.status_code == 200
    assert response.json() == {
        "chat_id": "111",
        "count": 2,
        "watchlist": [
            {"symbol": "AAPL"},
            {"symbol": "NVDA"},
        ],
    }


def test_rest_watchlist_earnings_scoped_to_chat_and_window(local_store):
    backend = get_backend()
    backend.add_to_watchlist("111", "AAPL")
    backend.add_to_watchlist("222", "NVDA")

    in_window = (date.today() + timedelta(days=3)).isoformat()
    out_of_window = (date.today() + timedelta(days=60)).isoformat()
    backend.upsert_earnings_calendar(
        [
            {"symbol": "AAPL", "date": in_window, "hour": "amc", "epsEstimate": 2.35},
            {"symbol": "AAPL", "date": out_of_window, "hour": "bmo"},
            {"symbol": "NVDA", "date": in_window, "hour": "amc"},  # not on 111's list
        ]
    )

    response = TestClient(app).get("/watchlist/111/earnings", params={"days": 14})

    assert response.status_code == 200
    body = response.json()
    assert body["chat_id"] == "111"
    assert body["count"] == 1
    row = body["earnings"][0]
    assert row["symbol"] == "AAPL"
    assert row["earning_date"] == in_window
    assert row["extended_hour"] == "amc"
    assert row["eps_estimate"] == 2.35


def test_rest_watchlist_earnings_empty_for_unknown_chat(local_store):
    response = TestClient(app).get("/watchlist/999/earnings")

    assert response.status_code == 200
    assert response.json()["count"] == 0


def _groq_text_response(text: str):
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )


@pytest.fixture
def mock_groq_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(market_summary, "_client", lambda: client)
    return client


@pytest.fixture
def mock_finnhub_news(monkeypatch):
    def fake_company_news(symbol, date_from, date_to):
        return [{"headline": f"{symbol} beats estimates", "summary": "s"}]

    def fake_tech_news():
        return [{"headline": "New AI model launched", "summary": ""}]

    monkeypatch.setattr(market_summary, "_fetch_company_news", fake_company_news)
    monkeypatch.setattr(market_summary, "_fetch_tech_news", fake_tech_news)


@pytest.fixture
def mock_index_quotes(monkeypatch):
    monkeypatch.setattr(
        market_summary, "get_quote", lambda symbol: {"current_price": 100.0, "change_percent": 1.0}
    )


def test_rest_market_summary_refreshes_and_generates_summary(
    local_store, mock_finnhub_news, mock_groq_client, mock_index_quotes
):
    mock_groq_client.chat.completions.create.return_value = _groq_text_response("摘要內容")

    response = TestClient(app).get("/market-summary", params={"days_back": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["stored_news"] == len(market_summary.BIG7) + 1  # 7 big7 + 1 AI item
    assert body["summary"].endswith("摘要內容")
    assert "summary_id" in body

    with sqlite3.connect(local_store) as conn:
        count = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    assert count == body["stored_news"]


def test_rest_market_summary_timeout_returns_504(local_store, monkeypatch):
    def boom(days_back):
        raise httpx.TimeoutException("finnhub timeout")

    monkeypatch.setattr(local_server, "refresh_news", boom)

    response = TestClient(app).get("/market-summary")

    assert response.status_code == 504


def test_rest_news_returns_stored_news_items(local_store, mock_finnhub_news):
    inserted = market_summary.refresh_news(days_back=1)

    response = TestClient(app).get("/news", params={"days_back": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == inserted
    assert len(body["news"]) == inserted
    headlines = {item["headline"] for item in body["news"]}
    assert "New AI model launched" in headlines
    assert any(item["symbol"] == "AAPL" and item["category"] == "big7" for item in body["news"])


def test_rest_market_summary_skips_groq_when_store_empty(
    local_store, monkeypatch, mock_groq_client, mock_index_quotes
):
    monkeypatch.setattr(market_summary, "_fetch_company_news", lambda *a, **k: [])
    monkeypatch.setattr(market_summary, "_fetch_tech_news", lambda: [])

    response = TestClient(app).get("/market-summary")

    assert response.status_code == 200
    body = response.json()
    assert body["stored_news"] == 0
    assert "請先執行新聞抓取" in body["summary"]
    mock_groq_client.chat.completions.create.assert_not_called()


class FinnhubStubHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    status = 200
    payload: dict | None = None
    raw_body: bytes | None = None
    delay = 0.0

    def do_GET(self):
        parsed = urlparse(self.path)
        self.__class__.requests.append(
            {"method": "GET", "path": parsed.path, "query": parse_qs(parsed.query)}
        )
        if self.__class__.delay:
            time.sleep(self.__class__.delay)
        self.send_response(self.__class__.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = self.__class__.raw_body
        if body is None:
            body = json.dumps(self.__class__.payload or {}).encode()
        self.wfile.write(body)

    def log_message(self, *_):
        return


@pytest.fixture
def finnhub_stub():
    FinnhubStubHandler.requests = []
    FinnhubStubHandler.status = 200
    FinnhubStubHandler.payload = {}
    FinnhubStubHandler.raw_body = None
    FinnhubStubHandler.delay = 0.0
    server = ThreadingHTTPServer(("127.0.0.1", 0), FinnhubStubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", FinnhubStubHandler
    finally:
        server.shutdown()
        server.server_close()


def _use_finnhub_stub(monkeypatch, base_url: str, timeout: float = 10.0):
    client = httpx.Client(base_url=base_url, timeout=timeout)
    monkeypatch.setattr(tools_http, "_client", client)
    return client


def test_earnings_calendar_fetch_writes_sqlite_with_local_finnhub_stub(
    local_store, finnhub_stub, monkeypatch
):
    base_url, handler = finnhub_stub
    handler.payload = {
        "earningsCalendar": [
            {
                "symbol": "AAPL",
                "date": "2026-07-23",
                "hour": "amc",
                "epsEstimate": 1.42,
                "revenueEstimate": 82000000000,
            },
            {
                "symbol": "MSFT",
                "date": "2026-07-24",
                "hour": "bmo",
                "epsEstimate": 3.1,
                "revenueEstimate": 70000000000,
            },
        ]
    }
    client = _use_finnhub_stub(monkeypatch, base_url)

    try:
        out = earnings.get_earnings_calendar("2026-07-20", "2026-07-24", symbol="AAPL")
        earnings.get_earnings_calendar("2026-07-20", "2026-07-24", symbol="AAPL")
    finally:
        client.close()

    assert out == [handler.payload["earningsCalendar"][0]]
    assert handler.requests[0]["method"] == "GET"
    assert handler.requests[0]["path"] == "/calendar/earnings"
    assert handler.requests[0]["query"] == {
        "from": ["2026-07-20"],
        "to": ["2026-07-24"],
        "symbol": ["AAPL"],
        "token": ["fake-finnhub"],
    }
    with sqlite3.connect(local_store) as conn:
        rows = conn.execute(
            """
            SELECT symbol, earning_date, extended_hour, eps_estimate, revenue_estimate
            FROM earnings_calendar
            """
        ).fetchall()
    assert rows == [("AAPL", "2026-07-23", "amc", 1.42, 82000000000)]


@pytest.mark.parametrize("status", [401, 429, 500])
def test_earnings_calendar_http_errors_do_not_write_sqlite(
    local_store, finnhub_stub, monkeypatch, status
):
    get_backend()
    base_url, handler = finnhub_stub
    handler.status = status
    handler.payload = {"error": "failed"}
    client = _use_finnhub_stub(monkeypatch, base_url)

    try:
        with pytest.raises(httpx.HTTPStatusError):
            earnings.get_earnings_calendar("2026-07-20", "2026-07-24")
    finally:
        client.close()

    with sqlite3.connect(local_store) as conn:
        count = conn.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()[0]
    assert count == 0


def test_earnings_calendar_malformed_response_does_not_write_sqlite(
    local_store, finnhub_stub, monkeypatch
):
    get_backend()
    base_url, handler = finnhub_stub
    handler.raw_body = b"{"
    client = _use_finnhub_stub(monkeypatch, base_url)

    try:
        with pytest.raises(json.JSONDecodeError):
            earnings.get_earnings_calendar("2026-07-20", "2026-07-24")
    finally:
        client.close()

    with sqlite3.connect(local_store) as conn:
        count = conn.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()[0]
    assert count == 0


def test_earnings_calendar_timeout_does_not_write_sqlite(
    local_store, finnhub_stub, monkeypatch
):
    get_backend()
    base_url, handler = finnhub_stub
    handler.delay = 0.2
    handler.payload = {"earningsCalendar": [{"symbol": "AAPL", "date": "2026-07-23"}]}
    client = _use_finnhub_stub(monkeypatch, base_url, timeout=0.01)

    try:
        with pytest.raises(httpx.TimeoutException):
            earnings.get_earnings_calendar("2026-07-20", "2026-07-24")
    finally:
        client.close()

    with sqlite3.connect(local_store) as conn:
        count = conn.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()[0]
    assert count == 0


def test_rest_earnings_returns_finnhub_envelope_and_writes_sqlite(
    local_store, finnhub_stub, monkeypatch
):
    base_url, handler = finnhub_stub
    handler.payload = {
        "earningsCalendar": [
            {
                "symbol": "AAPL",
                "date": "2026-07-23",
                "hour": "amc",
                "epsEstimate": 1.42,
                "revenueEstimate": 82000000000,
            },
            {
                "symbol": "MSFT",
                "date": "2026-07-24",
                "hour": "bmo",
                "epsEstimate": 3.1,
                "revenueEstimate": 70000000000,
            },
        ]
    }
    client = _use_finnhub_stub(monkeypatch, base_url)

    try:
        api = TestClient(app)
        response = api.get(
            "/earnings",
            params={
                "date_from": "2026-07-20",
                "date_to": "2026-07-24",
                "symbol": "AAPL",
            },
        )
        response2 = api.get(
            "/earnings",
            params={
                "date_from": "2026-07-20",
                "date_to": "2026-07-24",
                "symbol": "AAPL",
            },
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert response.json() == {"earningsCalendar": [handler.payload["earningsCalendar"][0]]}
    assert response2.status_code == 200
    assert handler.requests[0]["method"] == "GET"
    assert handler.requests[0]["path"] == "/calendar/earnings"
    assert handler.requests[0]["query"] == {
        "from": ["2026-07-20"],
        "to": ["2026-07-24"],
        "symbol": ["AAPL"],
        "token": ["fake-finnhub"],
    }
    with sqlite3.connect(local_store) as conn:
        rows = conn.execute(
            """
            SELECT symbol, earning_date, extended_hour, eps_estimate, revenue_estimate
            FROM earnings_calendar
            """
        ).fetchall()
    assert rows == [("AAPL", "2026-07-23", "amc", 1.42, 82000000000)]


def test_rest_earnings_rejects_invalid_date_range(local_store):
    response = TestClient(app).get(
        "/earnings",
        params={"date_from": "2026-07-24", "date_to": "2026-07-20"},
    )

    assert response.status_code == 422


def test_rest_earnings_rejects_invalid_date_format(local_store):
    response = TestClient(app).get(
        "/earnings",
        params={"date_from": "07/20/2026", "date_to": "2026-07-24"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("status", [401, 429, 500])
def test_rest_earnings_upstream_http_errors_do_not_leak_token_or_write_sqlite(
    local_store, finnhub_stub, monkeypatch, status
):
    get_backend()
    base_url, handler = finnhub_stub
    handler.status = status
    handler.payload = {"error": "failed"}
    client = _use_finnhub_stub(monkeypatch, base_url)

    try:
        response = TestClient(app).get(
            "/earnings",
            params={"date_from": "2026-07-20", "date_to": "2026-07-24"},
        )
    finally:
        client.close()

    assert response.status_code == 502
    assert "fake-finnhub" not in response.text
    with sqlite3.connect(local_store) as conn:
        count = conn.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()[0]
    assert count == 0


def test_rest_earnings_malformed_response_returns_502_and_does_not_write_sqlite(
    local_store, finnhub_stub, monkeypatch
):
    get_backend()
    base_url, handler = finnhub_stub
    handler.raw_body = b"{"
    client = _use_finnhub_stub(monkeypatch, base_url)

    try:
        response = TestClient(app).get(
            "/earnings",
            params={"date_from": "2026-07-20", "date_to": "2026-07-24"},
        )
    finally:
        client.close()

    assert response.status_code == 502
    assert "fake-finnhub" not in response.text
    with sqlite3.connect(local_store) as conn:
        count = conn.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()[0]
    assert count == 0


def test_rest_earnings_timeout_returns_504_and_does_not_write_sqlite(
    local_store, finnhub_stub, monkeypatch
):
    get_backend()
    base_url, handler = finnhub_stub
    handler.delay = 0.2
    handler.payload = {"earningsCalendar": [{"symbol": "AAPL", "date": "2026-07-23"}]}
    client = _use_finnhub_stub(monkeypatch, base_url, timeout=0.01)

    try:
        response = TestClient(app).get(
            "/earnings",
            params={"date_from": "2026-07-20", "date_to": "2026-07-24"},
        )
    finally:
        client.close()

    assert response.status_code == 504
    assert "fake-finnhub" not in response.text
    with sqlite3.connect(local_store) as conn:
        count = conn.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()[0]
    assert count == 0
