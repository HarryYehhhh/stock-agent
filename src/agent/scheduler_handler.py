"""EventBridge Scheduler Lambda: daily push of tomorrow's earnings to subscribers.

Runs once per day at 00:00 UTC (08:00 Asia/Taipei).

Local backend splits the flow in two steps:
- sync (cron: `python -m agent.scheduler_handler sync` / `make sync-earnings`):
  refresh upcoming earnings for all active subscribers' watchlist symbols into
  the sqlite earnings_calendar cache.
- notify: read tomorrow's earnings from sqlite only (warns when the cache looks
  stale), generate one Groq summary per reporting company cached in
  company_summaries, then compose each subscriber's push from those cached
  blocks — Groq is called at most once per company, not once per subscriber.

DynamoDB backend keeps the original live-fetch flow against the global
DEFAULT_WATCHLIST.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from aws_lambda_powertools import Logger

from agent.agent import run_agent
from agent.db.backend import get_backend, selected_backend_name
from agent.db.subscribers import get_all_active
from agent.db.watchlist import get_watchlist
from agent.telegram_client import send_message
from agent.tools.earnings import get_earnings_calendar

logger = Logger()

SYNC_DAYS = 30
MAX_DATA_AGE_HOURS = 24

PROMPT_TEMPLATE = (
    "明天（{tomorrow}）有以下固定追蹤清單公司公布財報：{earnings}\n\n"
    "請用繁體中文條列每家公司的：股票代號、財報時間（盤前/盤後）、"
    "分析師 EPS 預期、營收預期，並在最後附上 1-2 句整體市場注意事項。"
    "請列出上方傳入的每家公司，不要自行挑選或省略。"
)

COMPANY_PROMPT_TEMPLATE = (
    "明天（{tomorrow}）{symbol}將公布財報，快取資料：{row}\n\n"
    "請用繁體中文寫 2-3 句的財報前瞻摘要：財報時間（盤前/盤後/盤中）、"
    "分析師 EPS 預期與營收預期，以及一句該公司近期值得注意的重點。"
    "輸出使用 Telegram Markdown（粗體寫成 *text*，不要使用 ** 或 __），"
    "第一行以 *{symbol}* 開頭，不要加其他標題。"
)


def sync_earnings() -> list[str]:
    """Cron entry: refresh upcoming earnings for all active subscribers'
    watchlist symbols into the sqlite cache. Returns symbols that failed."""
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


def _is_stale(earnings: list[dict]) -> bool:
    latest = max(
        (row["updated_at"] for row in earnings if row.get("updated_at")),
        default=None,
    )
    if latest is None:
        return True
    age = datetime.now(timezone.utc) - datetime.fromisoformat(latest)
    return age > timedelta(hours=MAX_DATA_AGE_HOURS)


def _stale_banner(earnings: list[dict]) -> str:
    updated_at = max(
        (row["updated_at"] for row in earnings if row.get("updated_at")),
        default="未知",
    )
    return (
        f"⚠️ 財報快取資料已超過 {MAX_DATA_AGE_HOURS} 小時未更新"
        f"（最後同步：{updated_at}），財報時間可能有變動，請確認 sync cron 是否正常。\n\n"
    )


def _company_summary(backend, row: dict, tomorrow: str) -> str:
    """Return the cached Groq summary for this company's earnings, generating
    and caching it on first use. Falls back to raw data (uncached, so the next
    run retries) if generation fails."""
    symbol = row["symbol"]
    cached = backend.get_company_summary(symbol, tomorrow)
    if cached:
        return cached

    data = {k: v for k, v in row.items() if k != "chat_id"}
    try:
        summary = run_agent(
            COMPANY_PROMPT_TEMPLATE.format(
                tomorrow=tomorrow,
                symbol=symbol,
                row=data,
            ),
            [],
        )
    except Exception:
        logger.exception("summary generation failed for %s", symbol)
        return (
            f"*{symbol}*："
            f"時段 {row.get('extended_hour') or '未知'}、"
            f"EPS 預期 {row.get('eps_estimate') or '未知'}、"
            f"營收預期 {row.get('revenue_estimate') or '未知'}"
            "（摘要生成失敗，顯示原始資料）"
        )
    backend.upsert_company_summary(symbol, tomorrow, summary)
    return summary


def _notify_local(tomorrow: str) -> None:
    backend = get_backend()
    earnings = backend.get_watchlist_earnings_for_date(tomorrow)
    if not earnings:
        logger.info("no watchlist earnings tomorrow, skip push")
        return

    banner = _stale_banner(earnings) if _is_stale(earnings) else ""

    by_chat: dict[str, list[dict]] = {}
    for row in earnings:
        by_chat.setdefault(row["chat_id"], []).append(row)

    for chat_id, rows in by_chat.items():
        chat_id = str(chat_id)
        symbols = [row["symbol"] for row in rows]
        pending_symbols = backend.unnotified_symbols(chat_id, symbols, tomorrow)
        if not pending_symbols:
            continue

        blocks = [
            _company_summary(backend, row, tomorrow)
            for row in rows
            if row["symbol"] in pending_symbols
        ]
        body = (
            f"{banner}📊 *明日追蹤清單財報提醒（{tomorrow}）*\n\n"
            + "\n\n".join(blocks)
        )

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
        earnings.extend(rows)

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
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        failed = sync_earnings()
        print(f"sync done, failed symbols: {failed or '無'}")
    else:
        _run()
