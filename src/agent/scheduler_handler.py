"""EventBridge Scheduler Lambda: daily push of tomorrow's earnings to subscribers.

Runs once per day at 00:00 UTC (08:00 Asia/Taipei).
"""

from __future__ import annotations

from datetime import date, timedelta

from aws_lambda_powertools import Logger

from agent.agent import run_agent
from agent.db.subscribers import get_all_active
from agent.telegram_client import send_message
from agent.tools.earnings import get_earnings_calendar

logger = Logger()


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    earnings = get_earnings_calendar(tomorrow, tomorrow)

    if not earnings:
        logger.info("no earnings tomorrow, skip push")
        return {"statusCode": 200}

    subscribers = get_all_active()
    if not subscribers:
        logger.info("no active subscribers, skip push")
        return {"statusCode": 200}

    prompt = (
        f"明天（{tomorrow}）有以下美股公司公布財報：{earnings}\n\n"
        "請用繁體中文條列每家公司的：財報時間（盤前/盤後）、分析師 EPS 預期、"
        "營收預期，並在最後附上 1-2 句整體市場注意事項。"
        "若公司很多，挑出市值較大或關注度高的 5-10 家即可。"
    )
    summary = run_agent(prompt, [])
    body = f"📊 *明日財報提醒（{tomorrow}）*\n\n{summary}"

    for chat_id in subscribers:
        try:
            send_message(chat_id, body)
        except Exception:
            logger.exception("failed to push to %s", chat_id)

    return {"statusCode": 200}
