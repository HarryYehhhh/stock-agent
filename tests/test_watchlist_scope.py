"""Watchlist-scoped earnings queries: system-prompt injection of the caller's
watchlist symbols, the empty-watchlist block rule, and the earnings response
date/session formatting rule."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent import agent as agent_module
from agent import fast_path as fast_path_module
from agent import prompts as prompts_module
from agent.db.watchlist import add_to_watchlist


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


# --- prompts.watchlist_context (pure unit) ---


def test_watchlist_context_non_empty_lists_symbols_only():
    ctx = prompts_module.watchlist_context(["AAPL", "NVDA"])
    assert "AAPL" in ctx
    assert "NVDA" in ctx
    assert "get_earnings_calendar" in ctx
    # kept minimal: no extra per-symbol fields (dates, EPS, etc.), just the symbol list
    assert "epsEstimate" not in ctx


def test_watchlist_context_empty_returns_block_rule():
    ctx = prompts_module.watchlist_context([])
    assert ctx == prompts_module.WATCHLIST_EMPTY_CONTEXT
    assert "/watch" in ctx
    assert "不要呼叫任何財報相關工具" in ctx


# --- SYSTEM_INSTRUCTION: date / session formatting rule ---


def test_system_instruction_requires_full_date_and_session_label():
    assert "年月日" in agent_module.SYSTEM_INSTRUCTION
    assert "盤前" in agent_module.SYSTEM_INSTRUCTION
    assert "盤中" in agent_module.SYSTEM_INSTRUCTION
    assert "盤後" in agent_module.SYSTEM_INSTRUCTION
    assert "時段未定" in agent_module.SYSTEM_INSTRUCTION


# --- run_agent: watchlist injection ---


def test_run_agent_injects_watchlist_symbols_when_chat_id_given(local_store, mock_client):
    add_to_watchlist("111", "AAPL")
    add_to_watchlist("111", "NVDA")
    mock_client.chat.completions.create.return_value = _text_response("回覆")

    agent_module.run_agent("test", [], chat_id="111")

    messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
    system_messages = [m["content"] for m in messages if m["role"] == "system"]
    assert any("AAPL" in m and "NVDA" in m for m in system_messages)


def test_run_agent_no_watchlist_injection_when_chat_id_omitted(mock_client):
    mock_client.chat.completions.create.return_value = _text_response("回覆")

    agent_module.run_agent("test", [])

    messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
    system_messages = [m["content"] for m in messages if m["role"] == "system"]
    assert len(system_messages) == 1, "only the base system message, no watchlist context"


def test_run_agent_empty_watchlist_injects_block_rule(local_store, mock_client):
    mock_client.chat.completions.create.return_value = _text_response("回覆")

    agent_module.run_agent("明天有哪些公司發財報", [], chat_id="111")

    messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
    system_messages = [m["content"] for m in messages if m["role"] == "system"]
    assert any(m == prompts_module.WATCHLIST_EMPTY_CONTEXT for m in system_messages)


def test_run_agent_watchlists_scoped_per_chat_id(local_store, mock_client):
    add_to_watchlist("111", "AAPL")
    mock_client.chat.completions.create.return_value = _text_response("回覆")

    agent_module.run_agent("test", [], chat_id="222")

    messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
    system_messages = [m["content"] for m in messages if m["role"] == "system"]
    assert any(m == prompts_module.WATCHLIST_EMPTY_CONTEXT for m in system_messages)


# --- fast path interaction: empty watchlist must block explicit-ticker queries too ---


def test_run_agent_skips_fast_path_when_watchlist_empty(local_store, mock_client, monkeypatch):
    calendar_mock = MagicMock()
    monkeypatch.setattr(fast_path_module, "get_earnings_calendar", calendar_mock)
    mock_client.chat.completions.create.return_value = _text_response(
        "請先用 /watch 建立追蹤清單"
    )

    out = agent_module.run_agent("AAPL 財報是什麼時候", [], chat_id="111")

    calendar_mock.assert_not_called()
    mock_client.chat.completions.create.assert_called_once()
    assert out == "請先用 /watch 建立追蹤清單"


def test_run_agent_fast_path_still_hits_when_watchlist_non_empty(local_store, mock_client, monkeypatch):
    add_to_watchlist("111", "NVDA")
    monkeypatch.setattr(
        fast_path_module,
        "get_earnings_calendar",
        lambda date_from, date_to, symbol=None: [
            {"symbol": symbol, "date": "2026-10-29", "hour": "amc"}
        ],
    )

    out = agent_module.run_agent("AAPL 財報是什麼時候", [], chat_id="111")

    assert "AAPL" in out
    assert "2026-10-29" in out
    mock_client.chat.completions.create.assert_not_called()
