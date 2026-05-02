"""SQS-triggered Lambda that runs the Gemini agent for one Telegram update."""

from __future__ import annotations

import json

from aws_lambda_powertools import Logger

from agent.agent import run_agent
from agent.db.conversations import build_messages, save_message
from agent.db.subscribers import subscribe, unsubscribe
from agent.telegram_client import send_message, send_typing_action

logger = Logger()

WELCOME = (
    "👋 您好！我是美股財報 AI 助理\n\n"
    "可以問我：\n"
    "• 明天有哪些科技股要公布財報？\n"
    "• AAPL 最近的新聞\n"
    "• 看一下 NVDA 的分析師預期\n\n"
    "指令：\n"
    "/subscribe — 訂閱每日財報提醒（早上 08:00）\n"
    "/unsubscribe — 取消訂閱"
)


def _process_update(body: dict) -> None:
    message = body.get("message") or body.get("edited_message")
    if not message:
        return

    chat_id = str(message["chat"]["id"])
    text = (message.get("text") or "").strip()

    if not text:
        send_message(chat_id, "目前只支援文字訊息，請輸入您的問題。")
        return

    if text in {"/start", "/help"}:
        send_message(chat_id, WELCOME)
        return

    if text == "/subscribe":
        subscribe(chat_id)
        send_message(chat_id, "✅ 已訂閱每日財報提醒（早上 08:00 推播）")
        return

    if text == "/unsubscribe":
        unsubscribe(chat_id)
        send_message(chat_id, "❌ 已取消訂閱")
        return

    send_typing_action(chat_id)
    history = build_messages(chat_id)

    try:
        reply = run_agent(text, history)
    except Exception:
        logger.exception("agent failed for chat %s", chat_id)
        send_message(chat_id, "⚠️ 抱歉，剛才出了點問題，請稍後再試。")
        return

    save_message(chat_id, "user", text)
    save_message(chat_id, "assistant", reply)
    send_message(chat_id, reply)


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    for record in event.get("Records", []):
        try:
            body = json.loads(record["body"])
            _process_update(body)
        except Exception:
            # Log and swallow so a single malformed message doesn't block the batch.
            logger.exception("failed to process record")
    return {"statusCode": 200}
