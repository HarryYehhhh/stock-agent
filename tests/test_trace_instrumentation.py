"""run_agent() request-trace instrumentation (ADR-0013): one trace saved per
return path with `outcome` set correctly, full field coverage, save-failure
resilience, and zero behavioral change to messages/replies.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from groq import APIStatusError, BadRequestError, RateLimitError

from agent import agent as agent_module
from agent import fast_path as fast_path_module
from agent.db.backend import get_backend


def _text_response(text: str, prompt: int = 10, completion: int = 20):
    message = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(
        choices=[choice],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
    )


def _tool_response(tool_name: str, tool_args: dict, prompt: int = 15, completion: int = 5):
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name=tool_name, arguments=json.dumps(tool_args)),
    )
    message = SimpleNamespace(content=None, tool_calls=[tool_call])
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(
        choices=[choice],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
    )


def _bad_request_error(code: str, message: str = "tool call validation failed") -> BadRequestError:
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp = httpx.Response(
        400,
        request=req,
        json={"error": {"message": message, "type": "invalid_request_error", "code": code}},
    )
    return BadRequestError(f"Error code: 400 - {resp.json()}", response=resp, body=resp.json())


def _rate_limit_error() -> RateLimitError:
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp = httpx.Response(
        429,
        request=req,
        json={
            "error": {
                "message": "Rate limit reached for requests",
                "type": "requests",
                "code": "rate_limit_exceeded",
            }
        },
    )
    return RateLimitError(f"Error code: 429 - {resp.json()}", response=resp, body=resp.json())


@pytest.fixture
def mock_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(agent_module, "_client", lambda: client)
    return client


def _latest_trace(chat_id: str = "111") -> dict:
    traces = get_backend().list_traces(chat_id=chat_id)
    assert traces, "expected a saved trace"
    return traces[0]


# --- outcome coverage: one save_trace call per return path ---


def test_completed_outcome_with_tool_call_saves_full_trace(local_store, mock_client, monkeypatch):
    def fake_tool(symbol):
        return {"symbol": symbol, "buy": 10}

    monkeypatch.setitem(agent_module.TOOL_FUNCTIONS, "get_quote", fake_tool)
    mock_client.chat.completions.create.side_effect = [
        _tool_response("get_quote", {"symbol": "AAPL"}, prompt=15, completion=5),
        _text_response("AAPL 分析師評級：買入 10", prompt=30, completion=12),
    ]
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]

    out = agent_module.run_agent("AAPL 分析師怎麼看？", history, chat_id="111")

    assert out == "AAPL 分析師評級：買入 10"
    trace = _latest_trace()
    assert trace["outcome"] == "completed"
    assert trace["fast_path"] is False
    assert trace["chat_id"] == "111"
    assert trace["user_message"] == "AAPL 分析師怎麼看？"
    assert trace["history_msg_count"] == 2
    assert trace["history_char_count"] == len("hi") + len("hello")
    assert trace["iterations"] == 2
    assert trace["reply_char_count"] == len("AAPL 分析師評級：買入 10")
    assert trace["model"] == agent_module.MODEL
    assert trace["total_ms"] >= 0

    tools = trace["tools"]
    assert len(tools) == 1
    tool = tools[0]
    assert tool["name"] == "get_quote"
    assert tool["iteration"] == 1
    assert tool["result_type"] == "dict"
    assert tool["result_size"] == 2
    assert tool["ok"] is True
    assert tool["duration_ms"] >= 0
    assert json.loads(tool["args"]) == {"symbol": "AAPL"}

    assert trace["usage_by_iteration"] == [
        {"prompt": 15, "completion": 5},
        {"prompt": 30, "completion": 12},
    ]
    assert trace["usage"] == {
        "prompt_tokens": 45,
        "completion_tokens": 17,
        "total_tokens": 62,
    }


def test_completed_outcome_with_tool_call_and_playbook_hit_saves_trace(
    local_store, mock_client, monkeypatch
):
    """completed + 至少一次工具呼叫 + 命中 playbook 的組合：trace["playbook_hits"]
    須含正確的 source/score（contract Tests required › Trace 第二項字面要求）。"""

    def fake_tool(date_from, date_to, symbol=None):
        return [{"symbol": symbol or "AAPL", "date": "2026-08-01", "hour": "amc"}]

    monkeypatch.setitem(agent_module.TOOL_FUNCTIONS, "get_earnings_calendar", fake_tool)
    mock_client.chat.completions.create.side_effect = [
        _tool_response("get_earnings_calendar", {"date_from": "2026-07-27", "date_to": "2026-08-02"}),
        _text_response("本週財報：AAPL 8/1 盤後"),
    ]

    out = agent_module.run_agent("本週有什麼公司發布財報", [], chat_id="777")

    assert out == "本週財報：AAPL 8/1 盤後"
    trace = _latest_trace("777")
    assert trace["outcome"] == "completed"
    assert len(trace["tools"]) == 1
    assert trace["tools"][0]["name"] == "get_earnings_calendar"

    hits = trace["playbook_hits"]
    assert hits, "expected playbook_hits to be non-empty for a calibrated hit query"
    assert hits[0]["source"] == "earnings#財報行事曆時間查詢"
    assert hits[0]["score"] >= agent_module.MIN_SCORE


def test_loop_exceeded_outcome_saves_trace(local_store, mock_client):
    mock_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call_1",
                            function=SimpleNamespace(
                                name="nonexistent_tool", arguments=json.dumps({"symbol": "AAPL"})
                            ),
                        )
                    ],
                )
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )

    out = agent_module.run_agent("test", [], chat_id="222")

    assert out == "（推理超過最大迴圈次數，請換個問法重試）"
    trace = _latest_trace("222")
    assert trace["outcome"] == "loop_exceeded"
    assert trace["iterations"] == agent_module.MAX_TOOL_LOOPS


def test_groq_error_429_outcome_saves_trace(local_store, mock_client):
    mock_client.chat.completions.create.side_effect = _rate_limit_error()

    out = agent_module.run_agent("test", [], chat_id="333")

    assert out == "⏳ 今日 AI 額度已用完或請求過於頻繁，請稍後再試。"
    trace = _latest_trace("333")
    assert trace["outcome"] == "groq_error"


def test_tool_use_failed_exhausted_outcome_saves_trace(local_store, mock_client):
    mock_client.chat.completions.create.side_effect = [
        _bad_request_error("tool_use_failed") for _ in range(agent_module.MAX_TOOL_LOOPS)
    ]

    out = agent_module.run_agent("本月市場走勢如何?", [], chat_id="444")

    assert out == "⚠️ 工具呼叫連續失敗，請換個問法再試。"
    trace = _latest_trace("444")
    assert trace["outcome"] == "tool_use_failed_exhausted"


def test_fast_path_outcome_saves_trace(local_store, mock_client, monkeypatch):
    monkeypatch.setattr(
        fast_path_module,
        "get_earnings_calendar",
        lambda date_from, date_to, symbol=None: [
            {"symbol": symbol, "date": "2026-10-29", "hour": "amc"}
        ],
    )

    out = agent_module.run_agent("META的財報是什麼時候", [])

    mock_client.chat.completions.create.assert_not_called()
    trace = _latest_trace("__none__")
    assert trace["outcome"] == "fast_path"
    assert trace["fast_path"] is True
    assert trace["iterations"] == 0
    assert trace["tools"] == []
    assert trace["reply_char_count"] == len(out)


def test_run_agent_without_chat_id_uses_none_sentinel(local_store, mock_client):
    mock_client.chat.completions.create.return_value = _text_response("回覆")

    agent_module.run_agent("test", [])

    trace = _latest_trace("__none__")
    assert trace["chat_id"] == "__none__"


# --- save_trace failure must not affect the reply ---


def test_save_trace_failure_does_not_break_reply(local_store, mock_client, monkeypatch):
    mock_client.chat.completions.create.return_value = _text_response("回覆內容")

    def boom(_trace):
        raise RuntimeError("db down")

    monkeypatch.setattr(get_backend(), "save_trace", boom)

    out = agent_module.run_agent("test", [], chat_id="666")

    assert out == "回覆內容"


# --- zero-invasion: messages sent to Groq / reply text unaffected by trace ---


def test_zero_invasion_messages_and_reply_unaffected_by_trace(local_store, mock_client):
    mock_client.chat.completions.create.return_value = _text_response("這是回覆")

    out = agent_module.run_agent("本週有什麼公司發布財報", [])

    assert out == "這是回覆"
    messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
    system_messages = [m["content"] for m in messages if m["role"] == "system"]
    assert len(system_messages) == 2, "base system message + injected playbook context unchanged"
    assert "來源：earnings#財報行事曆時間查詢" in system_messages[1]
    assert messages[-1] == {"role": "user", "content": "本週有什麼公司發布財報"}
