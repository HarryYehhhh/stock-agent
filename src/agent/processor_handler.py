"""SQS-triggered Lambda that runs the Groq agent for one Telegram update."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from aws_lambda_powertools import Logger

from agent.agent import run_agent
from agent.db.conversations import build_messages, get_history, save_message
from agent.db.subscribers import subscribe, unsubscribe
from agent.db.watchlist import (
    add_to_watchlist,
    get_watchlist,
    remove_from_watchlist,
    seed_watchlist_if_empty,
)
from agent.telegram_client import send_message, send_typing_action

logger = Logger()

WATCH_USAGE = "用法：/watch <SYMBOL>，例如 /watch AAPL"
UNWATCH_USAGE = "用法：/unwatch <SYMBOL>，例如 /unwatch AAPL"
LOCAL_ONLY_MESSAGE = "⚠️ 目前僅本地模式支援自訂追蹤清單。"

REPLAY_WINDOW = timedelta(minutes=10)
REPLAY_PREFIX = "（與上題相同，以下為稍早的回覆）\n"

# US ticker：1-10 個字母，可選 . 或 - 接 1-3 個字母後綴（例如 BRK.B、BF-B）
SYMBOL_RE = re.compile(r"[A-Z]{1,10}([.\-][A-Z]{1,3})?")

WELCOME = (
    "👋 您好！我是美股財報 AI 助理\n\n"
    "可以問我：\n"
    "• 明天有哪些科技股要公布財報？\n"
    "• AAPL 最近的新聞\n"
    "• 看一下 NVDA 的分析師預期\n\n"
    "指令：\n"
    "/subscribe — 訂閱每日財報提醒（早上 08:00）\n"
    "/unsubscribe — 取消訂閱\n"
    "/watchlist — 查看我的追蹤清單\n"
    "/watch <SYMBOL> — 新增追蹤股票，例如 /watch AAPL\n"
    "/unwatch <SYMBOL> — 移除追蹤股票，例如 /unwatch AAPL"
)


def _find_replay(chat_id: str, text: str) -> str | None:
    """If the most recent stored exchange was the same user text (stripped)
    within REPLAY_WINDOW, return its stored assistant reply so the caller can
    resend it without calling run_agent(). Only checks the single most recent
    user/assistant pair; does not search further back in history."""
    history = get_history(chat_id, limit=2)
    if len(history) != 2:
        return None
    prev_user, prev_assistant = history
    if prev_user["role"] != "user" or prev_assistant["role"] != "assistant":
        return None
    if prev_user["content"].strip() != text:
        return None
    try:
        prev_time = datetime.fromisoformat(prev_user["timestamp"])
    except (KeyError, ValueError):
        return None
    if prev_time.tzinfo is None:
        prev_time = prev_time.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - prev_time > REPLAY_WINDOW:
        return None
    return prev_assistant["content"]


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
        seed_watchlist_if_empty(chat_id)
        send_message(chat_id, "✅ 已訂閱每日財報提醒（早上 08:00 推播）")
        return

    if text == "/unsubscribe":
        unsubscribe(chat_id)
        send_message(chat_id, "❌ 已取消訂閱")
        return

    if text == "/watchlist":
        items = get_watchlist(chat_id)
        if not items:
            send_message(
                chat_id, f"目前沒有追蹤任何股票。{WATCH_USAGE}"
            )
        else:
            lines = [f"• {item['symbol']}" for item in items]
            send_message(chat_id, "📋 您的追蹤清單：\n" + "\n".join(lines))
        return

    if text == "/watch" or text.startswith("/watch "):
        arg = text[len("/watch") :].strip()
        symbol = arg.upper()
        if not arg or not SYMBOL_RE.fullmatch(symbol):
            send_message(chat_id, WATCH_USAGE)
            return
        try:
            added = add_to_watchlist(chat_id, symbol)
        except NotImplementedError:
            send_message(chat_id, LOCAL_ONLY_MESSAGE)
            return
        if added:
            send_message(chat_id, f"✅ 已將 {symbol} 加入追蹤清單")
        else:
            send_message(chat_id, f"ℹ️ {symbol} 已經在您的追蹤清單中")
        return

    if text == "/unwatch" or text.startswith("/unwatch "):
        arg = text[len("/unwatch") :].strip()
        symbol = arg.upper()
        if not arg or not SYMBOL_RE.fullmatch(symbol):
            send_message(chat_id, UNWATCH_USAGE)
            return
        try:
            removed = remove_from_watchlist(chat_id, symbol)
        except NotImplementedError:
            send_message(chat_id, LOCAL_ONLY_MESSAGE)
            return
        if removed:
            send_message(chat_id, f"✅ 已將 {symbol} 從追蹤清單移除")
        else:
            send_message(chat_id, f"ℹ️ {symbol} 不在您的追蹤清單中")
        return

    replay_reply = _find_replay(chat_id, text)
    if replay_reply is not None:
        send_message(chat_id, REPLAY_PREFIX + replay_reply)
        return

    send_typing_action(chat_id)
    history = build_messages(chat_id)

    try:
        reply = run_agent(text, history, chat_id=chat_id)
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
