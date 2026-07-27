"""Fast-path shortcut for high-confidence "ticker 財報 日期" queries.

Verifies the regex-based positive/negative gating, the answer template, and
that a fast-path hit skips the Groq LLM entirely inside run_agent()."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent import agent as agent_module
from agent import fast_path as fast_path_module
from agent.fast_path import extract_ticker, try_fast_answer


# --- positive cases: high-confidence "ticker 財報 日期" phrasing ---


@pytest.mark.parametrize(
    "text,ticker",
    [
        ("META的財報是什麼時候", "META"),
        ("AAPL 財報日期", "AAPL"),
        ("NVDA 什麼時候發財報", "NVDA"),
        ("TSLA 財報何時公布", "TSLA"),
    ],
)
def test_try_fast_answer_hits_high_confidence_phrasing(monkeypatch, text, ticker):
    monkeypatch.setattr(
        fast_path_module,
        "get_earnings_calendar",
        lambda date_from, date_to, symbol=None: [
            {"symbol": symbol, "date": "2026-10-29", "hour": "amc", "epsEstimate": 1.93, "revenueEstimate": 110700000000}
        ],
    )

    out = try_fast_answer(text)

    assert out is not None
    assert ticker in out
    assert "2026-10-29" in out
    assert "盤後" in out


def test_try_fast_answer_no_results_message(monkeypatch):
    monkeypatch.setattr(
        fast_path_module, "get_earnings_calendar", lambda date_from, date_to, symbol=None: []
    )

    out = try_fast_answer("AAPL 財報日期")

    assert out == "未來 90 天內查無 AAPL 的財報日期"


def test_try_fast_answer_missing_hour_labeled_undetermined(monkeypatch):
    monkeypatch.setattr(
        fast_path_module,
        "get_earnings_calendar",
        lambda date_from, date_to, symbol=None: [{"symbol": symbol, "date": "2026-11-05", "hour": ""}],
    )

    out = try_fast_answer("AAPL 財報日期")

    assert "時段未定" in out


# --- negative cases: must fall through to None (寧可漏接不可誤攔) ---


@pytest.mark.parametrize(
    "text",
    [
        "本週有什麼公司發布財報",
        "META 最近新聞",
        "META",
        "META 的分析師預期",
        # non-ticker abbreviation not adjacent to 財報 (generic category question)
        "AI 相關企業的財報公布日期是什麼時候",
        # ticker-shaped token glued into a CJK compound, not a real ticker reference
        "美股AI概念股財報日期",
        # institutional abbreviation excluded outright
        "CNN 報導了哪些財報日期",
        # financial-metric abbreviation excluded outright, even though adjacent to 財報
        "EPS 財報日期怎麼看",
    ],
)
def test_try_fast_answer_negative_cases_return_none(monkeypatch, text):
    calendar_mock = MagicMock()
    monkeypatch.setattr(fast_path_module, "get_earnings_calendar", calendar_mock)

    assert try_fast_answer(text) is None
    calendar_mock.assert_not_called()


def test_try_fast_answer_finnhub_exception_returns_none(monkeypatch):
    def boom(date_from, date_to, symbol=None):
        raise RuntimeError("finnhub down")

    monkeypatch.setattr(fast_path_module, "get_earnings_calendar", boom)

    assert try_fast_answer("AAPL 財報日期") is None


def test_extract_ticker_lowercase_input_uppercased():
    assert extract_ticker("aapl 財報日期") == "AAPL"


def test_extract_ticker_no_ticker_returns_none():
    assert extract_ticker("本週有什麼公司發布財報") is None


# --- run_agent integration: fast-path hit skips the Groq client entirely ---


@pytest.fixture
def mock_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(agent_module, "_client", lambda: client)
    return client


def test_run_agent_fast_path_hit_skips_groq_client(mock_client, monkeypatch):
    monkeypatch.setattr(
        fast_path_module,
        "get_earnings_calendar",
        lambda date_from, date_to, symbol=None: [
            {"symbol": symbol, "date": "2026-10-29", "hour": "amc"}
        ],
    )

    out = agent_module.run_agent("META的財報是什麼時候", [])

    assert "META" in out
    assert "2026-10-29" in out
    mock_client.chat.completions.create.assert_not_called()


def test_run_agent_fast_path_miss_falls_through_to_groq(mock_client):
    message = SimpleNamespace(content="這是回覆", tool_calls=None)
    choice = SimpleNamespace(message=message)
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[choice], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20)
    )

    out = agent_module.run_agent("本週有什麼公司發布財報", [])

    assert out == "這是回覆"
    mock_client.chat.completions.create.assert_called_once()
