"""EventBridge Scheduler Lambda: daily push of tomorrow's earnings to subscribers.

Runs once per day at 00:00 UTC (08:00 Asia/Taipei).

Local backend: sync upcoming earnings into sqlite first, then notify per
subscriber from an earnings_calendar ⋈ watchlist ⋈ subscribers join, so each
subscriber only hears about their own watchlist symbols and a Finnhub outage
degrades to cached data instead of a missed push. DynamoDB backend keeps the
original live-fetch flow against the global DEFAULT_WATCHLIST.
"""

from __future__ import annotations

from datetime import date, timedelta

from aws_lambda_powertools import Logger

from agent.agent import run_agent
from agent.db.backend import get_backend, selected_backend_name
from agent.db.subscribers import get_all_active
from agent.db.watchlist import get_watchlist
from agent.telegram_client import send_message
from agent.tools.earnings import get_earnings_calendar

logger = Logger()

SYNC_DAYS = 30

PROMPT_TEMPLATE = (
    "明天（{tomorrow}）有以下固定追蹤清單公司公布財報：{earnings}\n\n"
    "請用繁體中文條列每家公司的：股票代號（交易所）、財報時間（盤前/盤後）、"
    "分析師 EPS 預期、營收預期，並在最後附上 1-2 句整體市場注意事項。"
    "交易所取自每筆資料的 exchange 欄位。"
    "請列出上方傳入的每家公司，不要自行挑選或省略。"
)


def _sync_earnings_calendar() -> list[str]:
    """Refresh upcoming earnings for all active subscribers' watchlist symbols.

    Returns the list of symbols whose Finnhub lookup failed.
    """
    today = date.today()
    date_to = (today + timedelta(days=SYNC_DAYS)).isoformat()
    failed = []
    for item in get_backend().get_active_watchlist_symbols():
        symbol = item["symbol"]
        try:
            get_earnings_calendar(today.isoformat(), date_to, symbol=symbol)
        except Exception:
            logger.exception("earnings sync failed for %s", symbol)
            failed.append(symbol)
    return failed


def _stale_banner(failed: list[str], earnings: list[dict]) -> str:
    updated_at = max(
        (row["updated_at"] for row in earnings if row.get("updated_at")),
        default="未知",
    )
    return (
        f"⚠️ 無法從 Finnhub 取得最新財報資料（{len(failed)} 檔查詢失敗），"
        f"以下使用快取資料（最後同步：{updated_at}），財報時間可能有變動。\n\n"
    )


def _notify_local(tomorrow: str) -> None:
    failed = _sync_earnings_calendar()
    backend = get_backend()
    earnings = backend.get_watchlist_earnings_for_date(tomorrow)
    if not earnings:
        if failed:
            logger.warning(
                "sync failed for %s and no cached earnings for %s", failed, tomorrow
            )
        else:
            logger.info("no watchlist earnings tomorrow, skip push")
        return

    banner = _stale_banner(failed, earnings) if failed else ""

    by_chat: dict[str, list[dict]] = {}
    for row in earnings:
        by_chat.setdefault(row["chat_id"], []).append(row)

    for chat_id, rows in by_chat.items():
        chat_id = str(chat_id)
        symbols = [row["symbol"] for row in rows]
        pending_symbols = backend.unnotified_symbols(chat_id, symbols, tomorrow)
        if not pending_symbols:
            continue

        pending_rows = [
            {k: v for k, v in row.items() if k != "chat_id"}
            for row in rows
            if row["symbol"] in pending_symbols
        ]
        summary = run_agent(
            PROMPT_TEMPLATE.format(tomorrow=tomorrow, earnings=pending_rows), []
        )
        body = f"{banner}📊 *明日追蹤清單財報提醒（{tomorrow}）*\n\n{summary}"

        try:
            send_message(chat_id, body)
        except Exception:
            logger.exception("failed to push to %s", chat_id)
            continue
        backend.mark_notified(chat_id, pending_symbols, tomorrow)


def _notify_live(tomorrow: str) -> None:
    earnings = []
    for item in get_watchlist():
        symbol = item["symbol"]
        try:
            rows = get_earnings_calendar(tomorrow, tomorrow, symbol=symbol)
        except Exception:
            logger.exception("earnings lookup failed for %s", symbol)
            continue
        earnings.extend({**row, "exchange": item["exchange"]} for row in rows)

    if not earnings:
        logger.info("no watchlist earnings tomorrow, skip push")
        return

    subscribers = get_all_active()
    if not subscribers:
        logger.info("no active subscribers, skip push")
        return

    summary = run_agent(PROMPT_TEMPLATE.format(tomorrow=tomorrow, earnings=earnings), [])
    body = f"📊 *明日追蹤清單財報提醒（{tomorrow}）*\n\n{summary}"

    for chat_id in subscribers:
        try:
            send_message(chat_id, body)
        except Exception:
            logger.exception("failed to push to %s", chat_id)


def _run() -> None:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    if selected_backend_name() == "local":
        _notify_local(tomorrow)
    else:
        _notify_live(tomorrow)


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    _run()
    return {"statusCode": 200}


if __name__ == "__main__":
    _run()
