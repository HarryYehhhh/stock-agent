"""Groq tool-use agent loop."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from functools import lru_cache

from groq import Groq

from agent.config import groq_api_key
from agent.tools import ALL_TOOL_SCHEMAS, TOOL_FUNCTIONS

log = logging.getLogger(__name__)

MODEL = "llama-3.3-70b-versatile"
MAX_TOOL_LOOPS = 10

SYSTEM_INSTRUCTION = (
    "你是專業美股財報分析助理。"
    "一律使用繁體中文回應；新聞英文標題與摘要主動翻譯成繁體中文。"
    "回覆新聞時不要逐條翻譯標題，而是依主題歸納整合，相同主題的多則新聞合併成一個重點；"
    "忽略行銷導流或無實質內容的條目（例如推廣性的清單文、訂閱/報名邀請）。"
    "輸出格式使用 Telegram Markdown：粗體寫成 *text*，程式碼寫成 `text`，不要使用 ** 或 __。"
    "EPS 用美元符號（$0.89），營收用 B/M 縮寫（24.6B、500M）。"
    "分析師評級顯示 買入/持有/賣出 數量分佈。"
    "Finnhub earnings hour 欄位代碼：bmo=盤前、amc=盤後、dmh=盤中。"
    "需要即時市場資料時主動呼叫工具，不要憑空猜測數字。"
    "\n\n工具選擇策略：\n"
    "當用戶提到明確的股票代碼並詢問財報、分析師預期或 EPS 時，"
    "同時呼叫 get_analyst_estimates（取得分析師共識）和 get_earnings_calendar（查詢財報發布日期）。"
    "查詢特定 ticker 的財報日期時，務必傳 symbol 參數給 get_earnings_calendar，避免漏掉冷門股。"
    "查詢即時股價或當日漲跌時呼叫 get_quote。"
    "詢問市場摘要、大盤整體狀況時呼叫 get_market_summary，不要用 get_quote 猜指數代號。"
    "如果財報尚未公布（發布日期晚於今天），在回覆中提醒用戶預計發布日期與時段。"
    "不要僅因 earnings calendar 查無結果就回覆『找不到資料』——分析師預期通常早於財報日期公布。"
    "\n\n遇到財報術語（EPS beat/miss、guidance、bmo/amc/dmh 等）、指數或代號對照"
    "（道瓊、S&P 500、那斯達克怎麼查價）、或分析方法論問題時，先呼叫 search_knowledge_base 查詢知識庫。"
    "指數代號不要憑空猜測後直接呼叫 get_quote（例如猜 DJIA），要先查知識庫取得對應 ETF 代號再查價。"
    "引用知識庫內容時，在回覆中附上來源標註（格式：檔名#標題）。"
    "知識庫查無相關內容時，明說不確定或知識庫沒有相關資料，不要編造答案。"
)

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
        response = _client().chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            temperature=0.7,
        )

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
