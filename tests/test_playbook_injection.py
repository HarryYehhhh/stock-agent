"""ADR-0009: playbook pre-injection in run_agent() — MIN_SCORE fallback,
injection format/placement, and observability (playbook_injection log,
_run_tool success log)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent import agent as agent_module


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


# --- _retrieve_playbook_hits / MIN_SCORE ---


def test_retrieve_playbook_hits_earnings_query_hits_calendar_section_only():
    hits = agent_module._retrieve_playbook_hits("本週有什麼公司發布財報")
    assert hits
    assert hits[0][0].source == "earnings#財報行事曆時間查詢"
    assert not any(section.source.startswith("stock-news#") for section, _ in hits)


def test_retrieve_playbook_hits_irrelevant_query_is_empty_below_min_score():
    assert agent_module._retrieve_playbook_hits("今天天氣如何") == []


def test_retrieve_playbook_hits_scores_meet_min_score():
    hits = agent_module._retrieve_playbook_hits("今天大盤怎樣")
    assert hits
    for _, score in hits:
        assert score >= agent_module.MIN_SCORE


# --- run_agent: injection vs. fallback ---


def test_run_agent_injects_playbook_context_for_matching_query(mock_client):
    mock_client.chat.completions.create.return_value = _text_response("這是回覆")

    agent_module.run_agent("本週有什麼公司發布財報", [])

    messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
    system_messages = [m["content"] for m in messages if m["role"] == "system"]
    assert len(system_messages) == 2, "base system message + injected playbook context"
    injected = system_messages[1]
    assert "來源：earnings#財報行事曆時間查詢" in injected
    assert "get_earnings_calendar" in injected


def test_run_agent_playbook_context_placed_after_base_system_before_history(mock_client):
    mock_client.chat.completions.create.return_value = _text_response("這是回覆")
    history = [{"role": "user", "content": "AAPL 分析師怎麼看？"}]

    agent_module.run_agent("本週有什麼公司發布財報", history)

    messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
    roles = [m["role"] for m in messages]
    # [base system, playbook system, history..., current user]
    assert roles[0] == "system"
    assert roles[1] == "system"
    assert roles[2] == "user"
    assert messages[2]["content"] == "AAPL 分析師怎麼看？"
    assert messages[-1]["content"] == "本週有什麼公司發布財報"


def test_run_agent_does_not_inject_for_irrelevant_query_fallback(mock_client):
    # "test" 對 playbook/exceptions 兩個索引都無命中（見 agent.knowledge.debug），
    # 純粹驗證「無 playbook 命中 -> 不注入」的 fallback 路徑，不誤觸離題攔截
    # （agent.py 的 off-topic pre-LLM intercept，見 test_off_topic_intercept.py）。
    mock_client.chat.completions.create.return_value = _text_response("測試回覆")

    agent_module.run_agent("test", [])

    messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
    system_messages = [m for m in messages if m["role"] == "system"]
    assert len(system_messages) == 1, "no playbook context should be injected (fallback)"


# --- observability ---


def test_playbook_injection_log_recorded_when_injected(mock_client, caplog):
    mock_client.chat.completions.create.return_value = _text_response("回覆")

    with caplog.at_level(logging.INFO):
        agent_module.run_agent("本週有什麼公司發布財報", [])

    records = [r for r in caplog.records if "playbook_injection" in r.message]
    assert records, "expected a playbook_injection log record"
    msg = records[0].message
    assert "injected=True" in msg
    assert "earnings#財報行事曆時間查詢" in msg


def test_playbook_injection_log_recorded_when_not_injected(mock_client, caplog):
    # "test"：無 playbook 命中且不誤觸離題攔截，見上方 fallback 測試的說明。
    mock_client.chat.completions.create.return_value = _text_response("回覆")

    with caplog.at_level(logging.INFO):
        agent_module.run_agent("test", [])

    records = [r for r in caplog.records if "playbook_injection" in r.message]
    assert records, "expected a playbook_injection log record"
    assert "injected=False" in records[0].message


def test_run_tool_success_logs_name_args_and_result_info(caplog):
    def fake_tool(symbol):
        return {"symbol": symbol, "buy": 10}

    from agent.tools import TOOL_FUNCTIONS

    original = TOOL_FUNCTIONS.get("get_quote")
    TOOL_FUNCTIONS["get_quote"] = fake_tool
    try:
        with caplog.at_level(logging.INFO):
            agent_module._run_tool("get_quote", {"symbol": "AAPL"})
    finally:
        if original is not None:
            TOOL_FUNCTIONS["get_quote"] = original

    records = [r for r in caplog.records if "get_quote" in r.message and "succeeded" in r.message]
    assert records, "expected a tool-success log record"
    msg = records[0].message
    assert "AAPL" in msg
    assert "dict" in msg
