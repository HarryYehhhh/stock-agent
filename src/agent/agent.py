"""Groq tool-use agent loop."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from functools import lru_cache

from groq import APIStatusError, BadRequestError, Groq

from agent.config import groq_api_key
from agent.knowledge.retrieval import PLAYBOOKS_DIR, Section, retrieve_scored
from agent.prompts import SYSTEM_INSTRUCTION
from agent.tools import ALL_TOOL_SCHEMAS, TOOL_FUNCTIONS

log = logging.getLogger(__name__)

MODEL = "llama-3.3-70b-versatile"
MAX_TOOL_LOOPS = 10

_TOKEN_RE = re.compile(r"token=[^&\s]+")

# --- Playbook pre-injection (ADR-0009) ---
# top_k=2 per spec/contract: at most the two best-matching playbook sections
# are candidates for injection.
PLAYBOOK_TOP_K = 2

# MIN_SCORE: unnormalized BM25 threshold (see agent.knowledge.retrieval) a
# playbook section's score must clear to be injected. Calibrated against
# knowledge/playbooks/ real content (2026-07-27, `python -m agent.knowledge.debug
# --source playbook`):
#   - genuine positive-class hits score >= ~6.9 (e.g. "本週有什麼公司發布財報" ->
#     earnings#財報行事曆時間查詢 = 10.3; "今天大盤怎樣" -> market-trends = 6.9;
#     "AAPL 有什麼新聞" -> stock-news = 7.3)
#   - cross-playbook noise from generic phrasing overlap (e.g. "有什麼") tops
#     out around 2.7; fully irrelevant queries (e.g. "今天天氣如何") top out
#     around 0.9 (coincidental single-token overlap)
# 3.5 sits above both noise bands and below every genuine hit observed.
MIN_SCORE = 3.5


def _retrieve_playbook_hits(query: str) -> list[tuple[Section, float]]:
    """Score the query against the playbook index and keep only sections
    at or above MIN_SCORE (top_k=2 candidates per PLAYBOOK_TOP_K)."""
    hits = retrieve_scored(query, top_k=PLAYBOOK_TOP_K, source_dir=PLAYBOOKS_DIR)
    return [(section, score) for section, score in hits if score >= MIN_SCORE]


def _build_playbook_context(hits: list[tuple[Section, float]]) -> str:
    """Build a system-message string injecting the matched playbook sections,
    each tagged with its 檔名#標題 source citation (D3)."""
    parts = [
        "以下是根據使用者當前問題比對到的操作指引（playbook），"
        "請依此指引選擇工具與組織回覆，不需要在回覆中提到你參考了指引：",
    ]
    for section, _score in hits:
        parts.append(f"\n[來源：{section.source}]\n{section.content}")
    return "\n".join(parts)


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
        size = len(result) if isinstance(result, (list, dict, str)) else None
        log.info(
            "tool %s succeeded: args=%s result_type=%s result_size=%s",
            name,
            _sanitize_error(json.dumps(tool_input, ensure_ascii=False, default=str)),
            type(result).__name__,
            size,
        )
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

    # Pre-injection (ADR-0009): retrieve against the *current* user message
    # only (never history, to structurally resist history-pollution) before
    # entering the tool loop.
    playbook_hits = _retrieve_playbook_hits(user_message)
    injected = bool(playbook_hits)
    log.info(
        "playbook_injection query=%r hits=%s injected=%s",
        user_message,
        [{"source": section.source, "score": round(score, 3)} for section, score in playbook_hits],
        injected,
    )
    if injected:
        messages.append({"role": "system", "content": _build_playbook_context(playbook_hits)})

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
        except APIStatusError as e:
            log.warning(
                "groq API error status=%s: %s", e.status_code, _sanitize_error(str(e))
            )
            return "⚠️ 這個問題需要的資料量超過目前額度限制，請把問題拆小或稍後再試。"

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
