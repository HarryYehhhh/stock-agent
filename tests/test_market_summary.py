"""Verify market summary fetch→store→summarize flow without real APIs."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent import market_summary as ms


def _text_response(text: str):
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
    )


@pytest.fixture
def mock_groq(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(ms, "_client", lambda: client)
    return client


@pytest.fixture
def mock_finnhub(monkeypatch):
    def fake_company_news(symbol, date_from, date_to):
        return [
            {"headline": "Shared cross-listed story", "summary": "s", "datetime": 1_800_000_000},
        ] + [
            {"headline": f"{symbol} story {i}", "summary": "s", "datetime": 1_800_000_000 - i}
            for i in range(5)
        ]

    def fake_tech_news():
        return [
            {"headline": "New AI model launched", "summary": "", "datetime": 1_800_000_000},
            {"headline": "Shared cross-listed story", "summary": "AI", "datetime": 1_800_000_000},
            {"headline": "Retail sales climb", "summary": "no keyword here", "datetime": 1_800_000_000},
        ]

    monkeypatch.setattr(ms, "_fetch_company_news", fake_company_news)
    monkeypatch.setattr(ms, "_fetch_tech_news", fake_tech_news)


QUOTES = {
    "SPY": {"current_price": 742.09, "change_percent": -0.16},
    "QQQ": {"current_price": 696.06, "change_percent": 0.105},
    "DIA": {"current_price": 517.94, "change_percent": -0.5511},
    "IWM": {"current_price": 292.31, "change_percent": -0.5884},
}


@pytest.fixture
def mock_quotes(monkeypatch):
    monkeypatch.setattr(ms, "get_quote", lambda symbol: dict(QUOTES[symbol]))


def test_ai_pattern_word_boundary():
    assert ms._AI_PATTERN.search("Nvidia bets big on AI chips")
    assert ms._AI_PATTERN.search("OpenAI releases new model")
    assert not ms._AI_PATTERN.search("The CEO said sales were fair")


def test_refresh_news_stores_and_dedupes(local_store, mock_finnhub):
    inserted = ms.refresh_news(days_back=1)

    # 7 symbols × (1 shared + 5 unique) minus 6 shared dups, plus 1 AI item
    # (shared tech headline deduped, non-AI headline filtered out)
    assert inserted == 7 * 5 + 1 + 1

    with sqlite3.connect(local_store) as conn:
        headlines = [r[0] for r in conn.execute("SELECT headline FROM news").fetchall()]
    assert headlines.count("Shared cross-listed story") == 1
    assert "Retail sales climb" not in headlines

    # re-running inserts nothing new
    assert ms.refresh_news(days_back=1) == 0


def test_refresh_news_survives_fetch_errors(local_store, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("finnhub down")

    monkeypatch.setattr(ms, "_fetch_company_news", boom)
    monkeypatch.setattr(ms, "_fetch_tech_news", boom)

    assert ms.refresh_news() == 0


def test_select_news_caps(local_store, mock_finnhub):
    ms.refresh_news(days_back=1)
    news = ms.select_news(days_back=1)

    assert len(news["ai"]) == 1
    symbols = [n["symbol"] for n in news["big7"]]
    for symbol in ms.BIG7:
        assert symbols.count(symbol) <= ms.MAX_NEWS_PER_SYMBOL


def test_fetch_index_quotes_skips_failures(monkeypatch):
    def flaky_quote(symbol):
        if symbol == "QQQ":
            raise RuntimeError("finnhub down")
        if symbol == "DIA":
            return {"current_price": 0, "change_percent": None}
        return dict(QUOTES[symbol])

    monkeypatch.setattr(ms, "get_quote", flaky_quote)
    quotes = ms.fetch_index_quotes()

    assert [q["symbol"] for q in quotes] == ["SPY", "IWM"]
    assert quotes[0] == {
        "name": "S&P 500", "symbol": "SPY", "close": 742.09, "change_percent": -0.16,
    }


def test_format_index_block():
    block = ms._format_index_block([
        {"name": "S&P 500", "symbol": "SPY", "close": 742.09, "change_percent": -0.16},
        {"name": "Nasdaq 100", "symbol": "QQQ", "close": 696.06, "change_percent": 0.105},
    ])

    assert block.splitlines() == [
        "*📈 大盤收盤指標*（ETF 代理）",
        "🔴 S&P 500 (SPY)：742.09（-0.16%）",
        "🟢 Nasdaq 100 (QQQ)：696.06（+0.10%）",
    ]


def test_generate_summary_reads_store_and_persists(
    local_store, mock_finnhub, mock_groq, mock_quotes
):
    ms.refresh_news(days_back=1)
    mock_groq.chat.completions.create.return_value = _text_response("*📊 Big 7 動態* 摘要內容")

    out = ms.generate_market_summary(days_back=1)

    assert out["summary"].startswith("*📈 大盤收盤指標*")
    assert "🔴 S&P 500 (SPY)：742.09（-0.16%）" in out["summary"]
    assert out["summary"].endswith("*📊 Big 7 動態* 摘要內容")
    assert [q["symbol"] for q in out["indexes"]] == ["SPY", "QQQ", "DIA", "IWM"]
    messages = mock_groq.chat.completions.create.call_args.kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert "繁體中文" in messages[0]["content"]
    assert "[大盤收盤]" in messages[1]["content"]
    assert "S&P 500 (SPY): 收盤 742.09, 漲跌 -0.16%" in messages[1]["content"]
    assert "AAPL story 0" in messages[1]["content"]
    assert "New AI model launched" in messages[1]["content"]

    # summary persisted with source news ids
    with sqlite3.connect(local_store) as conn:
        row = conn.execute(
            "SELECT id, summary, news_ids FROM market_summaries"
        ).fetchone()
    assert row[0] == out["summary_id"]
    assert row[1] == out["summary"]
    stored_ids = json.loads(row[2])
    expected_ids = [n["id"] for n in out["news"]["big7"] + out["news"]["ai"]]
    assert stored_ids == expected_ids


def test_generate_summary_skips_groq_when_store_empty(local_store, mock_groq, mock_quotes):
    out = ms.generate_market_summary()

    assert out["summary"].startswith("*📈 大盤收盤指標*")
    assert "請先執行新聞抓取" in out["summary"]
    mock_groq.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# push_daily_summary: CLI/cron entry
# ---------------------------------------------------------------------------


@pytest.fixture
def push_mocks(local_store, mock_finnhub, mock_groq, mock_quotes, monkeypatch):
    from agent.db.subscribers import subscribe

    mock_groq.chat.completions.create.return_value = _text_response("*📊 Big 7 動態* 摘要內容")
    fake_send = MagicMock()
    monkeypatch.setattr(ms, "send_message", fake_send)
    subscribe("111")
    subscribe("222")
    return {"send": fake_send}


def test_push_daily_summary_sends_to_all_active_subscribers(push_mocks):
    ms.push_daily_summary()

    assert push_mocks["send"].call_count == 2
    chat_ids = {call.args[0] for call in push_mocks["send"].call_args_list}
    assert chat_ids == {"111", "222"}
    for call in push_mocks["send"].call_args_list:
        assert "*📊 Big 7 動態* 摘要內容" in call.args[1]


def test_push_daily_summary_skips_send_when_no_news(local_store, mock_groq, mock_quotes, monkeypatch):
    from agent.db.subscribers import subscribe

    fake_send = MagicMock()
    monkeypatch.setattr(ms, "send_message", fake_send)
    subscribe("111")

    ms.push_daily_summary()

    fake_send.assert_not_called()
    mock_groq.chat.completions.create.assert_not_called()


def test_push_daily_summary_skips_send_when_no_subscribers(local_store, mock_finnhub, mock_groq, mock_quotes, monkeypatch):
    mock_groq.chat.completions.create.return_value = _text_response("*📊 Big 7 動態* 摘要內容")
    fake_send = MagicMock()
    monkeypatch.setattr(ms, "send_message", fake_send)

    ms.push_daily_summary()

    fake_send.assert_not_called()


def test_push_daily_summary_send_failure_does_not_stop_other_subscribers(push_mocks):
    push_mocks["send"].side_effect = [RuntimeError("telegram down"), None]

    ms.push_daily_summary()

    assert push_mocks["send"].call_count == 2


def test_push_daily_summary_end_to_end_on_dynamodb_backend(
    aws, mock_finnhub, mock_groq, mock_quotes, monkeypatch
):
    """Same flow as `push_mocks`, but against DynamoDBBackend (ADR-0006):
    news written to NewsTable, read back via list_news_since, summarized,
    and pushed — proving push_daily_summary/generate_market_summary/
    select_news/refresh_news work unmodified on the dynamodb backend too."""
    from agent.db.backend import get_backend
    from agent.db.subscribers import subscribe

    mock_groq.chat.completions.create.return_value = _text_response(
        "*📊 Big 7 動態* 摘要內容"
    )
    fake_send = MagicMock()
    monkeypatch.setattr(ms, "send_message", fake_send)
    subscribe("111")

    ms.push_daily_summary()

    assert fake_send.call_count == 1
    assert "*📊 Big 7 動態* 摘要內容" in fake_send.call_args.args[1]

    stored = get_backend().list_news_since("2000-01-01T00:00:00+00:00")
    assert len(stored) > 0
    assert all(row["id"] == row["headline"] for row in stored)
