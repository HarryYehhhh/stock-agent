"""Groq tool-use agent loop."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from functools import lru_cache

from groq import BadRequestError, Groq

from agent.config import groq_api_key
from agent.prompts import SYSTEM_INSTRUCTION
from agent.tools import ALL_TOOL_SCHEMAS, TOOL_FUNCTIONS

log = logging.getLogger(__name__)

MODEL = "llama-3.3-70b-versatile"
MAX_TOOL_LOOPS = 10

_TOKEN_RE = re.compile(r"token=[^&\s]+")


def _sanitize_error(msg: str) -> str:
    return _TOKEN_RE.sub("token=***", msg)


def _build_tools() -> list[dict]:
    """Convert tool schemas to OpenAI/Groq function definitions."""
    return [
        {
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema["input_schema"],
            },
        }
        for schema in ALL_TOOL_SCHEMAS
    ]


@lru_cache(maxsize=1)
def _client() -> Groq:
    return Groq(api_key=groq_api_key())


def _run_tool(name: str, tool_input: dict) -> dict:
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"unknown tool '{name}'"}
    try:
        result = fn(**tool_input)
        if isinstance(result, dict):
            return result
        return {"result": result}
    except Exception as e:
        log.warning("tool %s failed: %s", name, _sanitize_error(str(e)))
        return {"error": _sanitize_error(str(e)), "tool": name}


def _build_history_messages(history: list[dict]) -> list[dict]:
    """Map stored chat history to Groq chat messages (user/assistant roles)."""
    messages = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})
    return messages


def run_agent(user_message: str, history: list[dict]) -> str:
    """Run the Groq tool-use loop until completion. Returns the final assistant text."""
    messages: list[dict] = [
        {
            "role": "system",
            "content": f"今天日期是 {date.today().isoformat()}。\n{SYSTEM_INSTRUCTION}",
        }
    ]
    messages.extend(_build_history_messages(history))
    messages.append({"role": "user", "content": user_message})

    tools = _build_tools()

    for _ in range(MAX_TOOL_LOOPS):
        try:
            response = _client().chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,
                temperature=0.7,
            )
        except BadRequestError as e:
            error_code = e.body.get("error", {}).get("code") if isinstance(e.body, dict) else None
            if error_code != "tool_use_failed":
                raise
            log.warning("tool_use_failed, asking model to retry: %s", _sanitize_error(str(e)))
            messages.append({
                "role": "user",
                "content": (
                    "工具呼叫參數不符合規範，請依照以下錯誤訊息修正參數後重新呼叫工具：\n"
                    f"{_sanitize_error(str(e))}"
                ),
            })
            continue

        usage = getattr(response, "usage", None)
        if usage:
            log.info(
                "groq usage: input=%s output=%s",
                getattr(usage, "prompt_tokens", 0),
                getattr(usage, "completion_tokens", 0),
            )

        message = response.choices[0].message
        tool_calls = message.tool_calls
        if tool_calls:
            messages.append({
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            })

            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_result = _run_tool(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(tool_result, ensure_ascii=False),
                })
            continue

        return (message.content or "").strip()

    return "（推理超過最大迴圈次數，請換個問法重試）"
