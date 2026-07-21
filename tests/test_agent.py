"""Verify the Groq tool-use loop without hitting the Groq API."""

from __future__ import annotations

import json
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


def _tool_response(tool_name: str, tool_args: dict):
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name=tool_name, arguments=json.dumps(tool_args)),
    )
    message = SimpleNamespace(content=None, tool_calls=[tool_call])
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


def test_sanitize_error_removes_token():
    msg = "Error for https://finnhub.io/api/v1/stock?token=sk_12345&symbol=AAPL"
    sanitized = agent_module._sanitize_error(msg)
    assert "sk_12345" not in sanitized
    assert "token=***" in sanitized
    assert "symbol=AAPL" in sanitized


def test_sanitize_error_no_token_unchanged():
    msg = "connection timeout"
    assert agent_module._sanitize_error(msg) == msg


def test_build_history_maps_roles():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "bye"},
    ]
    messages = agent_module._build_history_messages(history)
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert [m["content"] for m in messages] == ["hi", "hello", "bye"]


def test_build_history_empty():
    assert agent_module._build_history_messages([]) == []


def test_uses_groq_model():
    assert "llama" in agent_module.MODEL


def test_system_instruction_is_string():
    assert isinstance(agent_module.SYSTEM_INSTRUCTION, str)
    assert "繁體中文" in agent_module.SYSTEM_INSTRUCTION
    assert "get_analyst_estimates" in agent_module.SYSTEM_INSTRUCTION
    assert "get_earnings_calendar" in agent_module.SYSTEM_INSTRUCTION


def test_build_tools_returns_function_definitions():
    tools = agent_module._build_tools()
    names = {t["function"]["name"] for t in tools}
    assert all(t["type"] == "function" for t in tools)
    assert names == {
        "get_earnings_calendar",
        "get_analyst_estimates",
        "get_stock_news",
        "get_quote",
    }


def test_simple_text_response(mock_client):
    mock_client.chat.completions.create.return_value = _text_response("您好")
    out = agent_module.run_agent("hi", [])
    assert out == "您好"


def test_tool_use_loop_dispatches_and_continues(mock_client, monkeypatch):
    captured: dict = {}

    def fake_tool(symbol):
        captured["symbol"] = symbol
        return {"symbol": symbol, "buy": 10}

    monkeypatch.setitem(agent_module.TOOL_FUNCTIONS, "get_analyst_estimates", fake_tool)

    mock_client.chat.completions.create.side_effect = [
        _tool_response("get_analyst_estimates", {"symbol": "AAPL"}),
        _text_response("AAPL 分析師評級：買入 10"),
    ]

    out = agent_module.run_agent("AAPL 分析師怎麼看？", [])
    assert out == "AAPL 分析師評級：買入 10"
    assert captured["symbol"] == "AAPL"
    assert mock_client.chat.completions.create.call_count == 2


def test_unknown_tool_does_not_crash(mock_client):
    mock_client.chat.completions.create.side_effect = [
        _tool_response("nonexistent_tool", {}),
        _text_response("處理完成"),
    ]
    out = agent_module.run_agent("test", [])
    assert out == "處理完成"


def test_tool_exception_returned_as_error_to_groq(mock_client, monkeypatch):
    def boom(**_):
        raise RuntimeError("finnhub down")

    monkeypatch.setitem(agent_module.TOOL_FUNCTIONS, "get_analyst_estimates", boom)
    mock_client.chat.completions.create.side_effect = [
        _tool_response("get_analyst_estimates", {"symbol": "AAPL"}),
        _text_response("已處理錯誤"),
    ]

    out = agent_module.run_agent("test", [])
    assert out == "已處理錯誤"
