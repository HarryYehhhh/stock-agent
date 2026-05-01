"""Verify the Gemini tool-use loop without hitting Google API."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent import agent as agent_module


def _text_response(text: str):
    part = SimpleNamespace(text=text, function_call=None)
    candidate = SimpleNamespace(
        content=SimpleNamespace(role="model", parts=[part]),
    )
    return SimpleNamespace(
        candidates=[candidate],
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=20),
    )


def _tool_response(tool_name: str, tool_args: dict):
    fc = SimpleNamespace(name=tool_name, args=tool_args)
    part = SimpleNamespace(text=None, function_call=fc)
    candidate = SimpleNamespace(
        content=SimpleNamespace(role="model", parts=[part]),
    )
    return SimpleNamespace(
        candidates=[candidate],
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=20),
    )


@pytest.fixture
def mock_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(agent_module, "_client", lambda: client)
    return client


def test_uses_gemini_model():
    assert "gemini" in agent_module.MODEL


def test_system_instruction_is_string():
    assert isinstance(agent_module.SYSTEM_INSTRUCTION, str)
    assert "繁體中文" in agent_module.SYSTEM_INSTRUCTION


def test_build_tools_returns_list():
    tools = agent_module._build_tools()
    assert len(tools) == 1
    declarations = tools[0].function_declarations
    names = {d.name for d in declarations}
    assert names == {"get_earnings_calendar", "get_analyst_estimates", "get_stocks_by_sector", "get_stock_news"}


def test_simple_text_response(mock_client):
    mock_client.models.generate_content.return_value = _text_response("您好")
    out = agent_module.run_agent("hi", [])
    assert out == "您好"


def test_tool_use_loop_dispatches_and_continues(mock_client, monkeypatch):
    captured: dict = {}

    def fake_tool(symbol):
        captured["symbol"] = symbol
        return {"symbol": symbol, "buy": 10}

    monkeypatch.setitem(agent_module.TOOL_FUNCTIONS, "get_analyst_estimates", fake_tool)

    mock_client.models.generate_content.side_effect = [
        _tool_response("get_analyst_estimates", {"symbol": "AAPL"}),
        _text_response("AAPL 分析師評級：買入 10"),
    ]

    out = agent_module.run_agent("AAPL 分析師怎麼看？", [])
    assert out == "AAPL 分析師評級：買入 10"
    assert captured["symbol"] == "AAPL"
    assert mock_client.models.generate_content.call_count == 2


def test_unknown_tool_does_not_crash(mock_client):
    mock_client.models.generate_content.side_effect = [
        _tool_response("nonexistent_tool", {}),
        _text_response("處理完成"),
    ]
    out = agent_module.run_agent("test", [])
    assert out == "處理完成"


def test_tool_exception_returned_as_error_to_gemini(mock_client, monkeypatch):
    def boom(**_):
        raise RuntimeError("finnhub down")

    monkeypatch.setitem(agent_module.TOOL_FUNCTIONS, "get_analyst_estimates", boom)
    mock_client.models.generate_content.side_effect = [
        _tool_response("get_analyst_estimates", {"symbol": "AAPL"}),
        _text_response("已處理錯誤"),
    ]

    out = agent_module.run_agent("test", [])
    assert out == "已處理錯誤"
