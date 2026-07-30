"""非美股相關問題 pre-LLM 攔截：命中 knowledge/exceptions/off-topic.md 且未命中
playbook 時直接回固定句，不進 Groq；命中 playbook 或 fast path 的問題不受影響。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent import agent as agent_module
from agent import fast_path as fast_path_module
from agent.db.backend import get_backend


def _text_response(text: str):
    message = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(
        choices=[choice],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
    )


@pytest.fixture
def mock_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(agent_module, "_client", lambda: client)
    return client


def _latest_trace(chat_id: str = "__none__") -> dict:
    traces = get_backend().list_traces(chat_id=chat_id)
    assert traces, "expected a saved trace"
    return traces[0]


# --- _is_off_topic ---


def test_is_off_topic_true_for_weather_query():
    assert agent_module._is_off_topic("今天天氣如何?")


def test_is_off_topic_false_for_kb_terminology_query():
    assert not agent_module._is_off_topic("什麼是 EPS")
    assert not agent_module._is_off_topic("本益比是什麼")


# --- run_agent: off-topic short-circuit ---


def test_run_agent_off_topic_query_returns_fixed_reply_without_calling_groq(mock_client):
    out = agent_module.run_agent("今天天氣如何?", [])

    assert out == "非美股相關問題無法回答"
    mock_client.chat.completions.create.assert_not_called()


def test_run_agent_off_topic_query_saves_off_topic_outcome_trace(local_store, mock_client):
    agent_module.run_agent("今天天氣如何?", [])

    trace = _latest_trace()
    assert trace["outcome"] == "off_topic"
    assert trace["playbook_hits"] == []
    assert trace["fast_path"] is False
    assert trace["iterations"] == 0
    assert trace["tools"] == []


def test_run_agent_playbook_hit_query_is_not_intercepted(mock_client):
    """命中 playbook 的問句（財報/新聞/大盤）即使碰巧也對例外語料有分數，
    仍應照常進 LLM loop，不被攔截。"""
    mock_client.chat.completions.create.return_value = _text_response("本週財報如下")

    out = agent_module.run_agent("本週有什麼公司發布財報", [])

    assert out == "本週財報如下"
    mock_client.chat.completions.create.assert_called_once()


def test_run_agent_stock_news_query_is_not_intercepted(mock_client):
    mock_client.chat.completions.create.return_value = _text_response("AAPL 最新消息")

    out = agent_module.run_agent("AAPL 有什麼新聞", [])

    assert out == "AAPL 最新消息"
    mock_client.chat.completions.create.assert_called_once()


def test_run_agent_market_summary_query_is_not_intercepted(mock_client):
    mock_client.chat.completions.create.return_value = _text_response("大盤現況如下")

    out = agent_module.run_agent("今天大盤怎樣", [])

    assert out == "大盤現況如下"
    mock_client.chat.completions.create.assert_called_once()


def test_run_agent_kb_terminology_query_is_not_intercepted(mock_client):
    """知識庫名詞問題不命中 playbook，得靠例外語料分數不過門檻來保護。"""
    mock_client.chat.completions.create.return_value = _text_response("EPS 是每股盈餘")

    out = agent_module.run_agent("什麼是 EPS", [])

    assert out == "EPS 是每股盈餘"
    mock_client.chat.completions.create.assert_called_once()


def test_run_agent_pe_ratio_kb_query_is_not_intercepted(mock_client):
    mock_client.chat.completions.create.return_value = _text_response("本益比是股價除以每股盈餘")

    out = agent_module.run_agent("本益比是什麼", [])

    assert out == "本益比是股價除以每股盈餘"
    mock_client.chat.completions.create.assert_called_once()


def test_run_agent_fast_path_query_still_uses_fast_path(mock_client, monkeypatch):
    """fast path（ticker 財報日期）維持最優先，攔截檢查不影響 fast path。"""
    monkeypatch.setattr(
        fast_path_module,
        "get_earnings_calendar",
        lambda date_from, date_to, symbol=None: [
            {"symbol": symbol, "date": "2026-10-29", "hour": "amc"}
        ],
    )

    out = agent_module.run_agent("META的財報是什麼時候", [])

    mock_client.chat.completions.create.assert_not_called()
    assert "2026-10-29" in out or "META" in out
