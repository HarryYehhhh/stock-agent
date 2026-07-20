"""EventBridge Scheduler Lambda: daily push of tomorrow's earnings to subscribers.

Runs once per day at 00:00 UTC (08:00 Asia/Taipei).
"""

from __future__ import annotations

from datetime import date, timedelta

from aws_lambda_powertools import Logger

from agent.agent import run_agent
from agent.db.subscribers import get_all_active
from agent.db.watchlist import get_watchlist
from agent.telegram_client import send_message
from agent.tools.earnings import get_earnings_calendar

logger = Logger()


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    earnings = []
    for item in get_watchlist():
        ticker = item["ticker"]
        try:
            rows = get_earnings_calendar(tomorrow, tomorrow, symbol=ticker)
        except Exception:
            logger.exception("earnings lookup failed for %s", ticker)
            continue
        earnings.extend({**row, "exchange": item["exchange"]} for row in rows)

    if not earnings:
        logger.info("no watchlist earnings tomorrow, skip push")
        return {"statusCode": 200}

    subscribers = get_all_active()
    if not subscribers:
        logger.info("no active subscribers, skip push")
        return {"statusCode": 200}

    prompt = (
        f"明天（{tomorrow}）有以下固定追蹤清單公司公布財報：{earnings}\n\n"
        "請用繁體中文條列每家公司的：股票代號（交易所）、財報時間（盤前/盤後）、"
        "分析師 EPS 預期、營收預期，並在最後附上 1-2 句整體市場注意事項。"
        "交易所取自每筆資料的 exchange 欄位。"
        "請列出上方傳入的每家公司，不要自行挑選或省略。"
    )
    summary = run_agent(prompt, [])
    body = f"📊 *明日追蹤清單財報提醒（{tomorrow}）*\n\n{summary}"

    for chat_id in subscribers:
        try:
            send_message(chat_id, body)
        except Exception:
            logger.exception("failed to push to %s", chat_id)

    return {"statusCode": 200}
