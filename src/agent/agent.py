"""Gemini tool-use agent loop."""

from __future__ import annotations

import logging
from functools import lru_cache

from google import genai
from google.genai import types

from agent.config import gemini_api_key
from agent.tools import ALL_TOOL_SCHEMAS, TOOL_FUNCTIONS

log = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash"
MAX_TOOL_LOOPS = 10

SYSTEM_INSTRUCTION = (
    "你是專業美股財報分析助理。"
    "一律使用繁體中文回應；新聞英文標題與摘要主動翻譯成繁體中文。"
    "輸出格式使用 Telegram Markdown：粗體寫成 *text*，程式碼寫成 `text`，不要使用 ** 或 __。"
    "EPS 用美元符號（$0.89），營收用 B/M 縮寫（24.6B、500M）。"
    "分析師評級顯示 買入/持有/賣出 數量分佈。"
    "Finnhub earnings hour 欄位代碼：bmo=盤前、amc=盤後、dmh=盤中。"
    "需要即時市場資料時主動呼叫工具，不要憑空猜測數字。"
)


def _build_tools() -> list[types.Tool]:
    """Convert tool schemas to Gemini function declarations."""
    declarations = []
    for schema in ALL_TOOL_SCHEMAS:
        props = {}
        required = schema["input_schema"].get("required", [])
        for name, prop in schema["input_schema"]["properties"].items():
            schema_type = prop.get("type", "string").upper()
            props[name] = types.Schema(
                type=schema_type,
                description=prop.get("description", ""),
            )
        declarations.append(types.FunctionDeclaration(
            name=schema["name"],
            description=schema["description"],
            parameters=types.Schema(
                type="OBJECT",
                properties=props,
                required=required,
            ),
        ))
    return [types.Tool(function_declarations=declarations)]


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    return genai.Client(api_key=gemini_api_key())


def _run_tool(name: str, tool_input: dict) -> dict:
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"unknown tool '{name}'"}
    try:
        result = fn(**tool_input)
        if isinstance(result, str):
            return {"result": result}
        return result
    except Exception as e:
        log.exception("tool %s failed", name)
        return {"error": str(e), "tool": name}


def run_agent(user_message: str, history: list[dict]) -> str:
    """Run the Gemini tool-use loop until completion. Returns the final assistant text."""
    contents: list[types.Content] = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(
            role=role,
            parts=[types.Part.from_text(text=msg["content"])],
        ))

    contents.append(types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_message)],
    ))

    tools = _build_tools()

    for _ in range(MAX_TOOL_LOOPS):
        response = _client().models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=tools,
                temperature=0.7,
            ),
        )

        usage = getattr(response, "usage_metadata", None)
        if usage:
            log.info(
                "gemini usage: input=%s output=%s",
                getattr(usage, "prompt_token_count", 0),
                getattr(usage, "candidates_token_count", 0),
            )

        candidate = response.candidates[0]
        parts = candidate.content.parts

        function_calls = [p for p in parts if p.function_call]
        if function_calls:
            contents.append(candidate.content)

            function_responses = []
            for fc_part in function_calls:
                fc = fc_part.function_call
                tool_result = _run_tool(fc.name, dict(fc.args))
                function_responses.append(
                    types.Part.from_function_response(
                        name=fc.name,
                        response=tool_result,
                    )
                )

            contents.append(types.Content(
                role="user",
                parts=function_responses,
            ))
            continue

        text_parts = [p.text for p in parts if p.text]
        return "\n".join(text_parts).strip()

    return "（推理超過最大迴圈次數，請換個問法重試）"
