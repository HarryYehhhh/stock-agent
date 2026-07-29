"""Structural invariant (ADR-0014): run_agent()'s injection layer never reads
the corrections table. Spies on the active backend's correction-reading
methods and asserts zero calls across a full run_agent() pass (playbook
injection hit + a tool call)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent import agent as agent_module
from agent.db.backend import get_backend


@pytest.fixture
def mock_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(agent_module, "_client", lambda: client)
    return client


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


def _text_response(text: str):
    message = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(
        choices=[choice],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
    )


def test_run_agent_never_reads_corrections_table(local_store, mock_client, monkeypatch):
    def fake_tool(symbol):
        return {"symbol": symbol, "buy": 10}

    monkeypatch.setitem(agent_module.TOOL_FUNCTIONS, "get_quote", fake_tool)
    mock_client.chat.completions.create.side_effect = [
        _tool_response("get_quote", {"symbol": "AAPL"}),
        _text_response("AAPL 分析師評級：買入 10"),
    ]

    backend = get_backend()
    list_corrections_spy = MagicMock(wraps=backend.list_corrections)
    save_correction_spy = MagicMock(wraps=backend.save_correction)
    update_status_spy = MagicMock(wraps=backend.update_correction_status)
    monkeypatch.setattr(backend, "list_corrections", list_corrections_spy)
    monkeypatch.setattr(backend, "save_correction", save_correction_spy)
    monkeypatch.setattr(backend, "update_correction_status", update_status_spy)

    out = agent_module.run_agent("AAPL 分析師怎麼看？", [], chat_id="111")

    assert out == "AAPL 分析師評級：買入 10"
    list_corrections_spy.assert_not_called()
    save_correction_spy.assert_not_called()
    update_status_spy.assert_not_called()
