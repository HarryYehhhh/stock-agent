"""EventBridge Scheduler Lambda: daily earnings sync + notify + market summary.

Cloud (`STORAGE_BACKEND=dynamodb`) splits the daily pipeline across two
EventBridge Scheduler events, each invoking `handler()` with a distinct
`mode` payload (ADR-0007):
- `{"mode": "sync"}` (08:00 Asia/Taipei): fetch/write stages only — sync
  earnings calendar and refresh Big7/AI news into DynamoDB.
- `{"mode": "notify"}` (10:00 Asia/Taipei): read/notify stages only — push
  watchlist earnings notifications for earnings releasing within the next 24
  hours, then the daily market summary (without re-fetching news; the 08:00
  sync already did that).

A missing or unknown `mode` is a fail-fast: `handler()` raises before running
any stage, so a scheduler misconfiguration (e.g. a dropped `Input` payload)
surfaces as a Lambda `Errors` metric instead of silently running the wrong
stage at the wrong time.

Local (launchd) splits the same pipeline across separate cron jobs/make
targets instead of a single invocation:
- sync (`python -m agent.scheduler_handler sync` / `make sync-earnings`):
  refresh upcoming earnings for all active subscribers' watchlist symbols into
  the sqlite earnings_calendar cache.
- notify (`python -m agent.scheduler_handler` / `make notify-earnings`): read
  from sqlite only the earnings releasing within the next 24 hours (warns
  when the cache looks stale),
  generate one Groq summary per reporting company cached in company_summaries,
  then compose each subscriber's push from those cached blocks — Groq is
  called at most once per company, not once per subscriber.
- daily summary (`make daily-summary`): see `agent.market_summary`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from aws_lambda_powertools import Logger

from agent.agent import NON_CACHEABLE_FALLBACK_MESSAGES, run_agent
from agent.db.backend import get_backend
from agent.market_summary import push_daily_summary, refresh_news
from agent.prompts import COMPANY_PROMPT_TEMPLATE
from agent.telegram_client import send_message
from agent.tools.earnings import get_earnings_calendar

logger = Logger()

SYNC_DAYS = 30
MAX_DATA_AGE_HOURS = 24


def sync_earnings() -> list[str]:
    """Cron entry: refresh upcoming earnings for all active subscribers'
    watchlist symbols into the sqlite cache. Returns symbols that failed."""
    today = date.today()
    date_to = (today + timedelta(days=SYNC_DAYS)).isoformat()
    failed = []
    backend = get_backend()
    symbols = backend.get_active_watchlist_symbols()
    for item in symbols:
        symbol = item["symbol"]
        try:
            rows = get_earnings_calendar(today.isoformat(), date_to, symbol=symbol)
        except Exception:
            logger.exception("earnings sync failed for %s", symbol)
            failed.append(symbol)
            continue
        try:
            keep_dates = [row["date"] for row in rows if row.get("date")]
            backend.delete_earnings_calendar_except(symbol, keep_dates, today.isoformat())
        except Exception:
            logger.exception("earnings calendar cleanup failed for %s", symbol)
    logger.info(
        "sync_earnings: synced %d/%d symbols (failed=%s)",
        len(symbols) - len(failed), len(symbols), failed,
    )
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


def _company_summary(backend, row: dict, target_date: str) -> str:
    """Return the cached Groq summary for this company's earnings, generating
    and caching it on first use. Falls back to raw data (uncached, so the next
    run retries) if generation fails or run_agent() itself returns a
    Groq/tool-loop failure fallback message (NON_CACHEABLE_FALLBACK_MESSAGES) —
    otherwise that error text would get cached as if it were a real summary."""
    symbol = row["symbol"]
    cached = backend.get_company_summary(symbol, target_date)
    if cached:
        return cached

    def raw_data_fallback() -> str:
        return (
            f"*{symbol}*："
            f"時段 {row.get('extended_hour') or '未知'}、"
            f"EPS 預期 {row.get('eps_estimate') or '未知'}、"
            f"營收預期 {row.get('revenue_estimate') or '未知'}"
            "（摘要生成失敗，顯示原始資料）"
        )

    data = {k: v for k, v in row.items() if k != "chat_id"}
    try:
        summary = run_agent(
            COMPANY_PROMPT_TEMPLATE.format(
                target_date=target_date,
                symbol=symbol,
                row=data,
            ),
            [],
        )
    except Exception:
        logger.exception("summary generation failed for %s", symbol)
        return raw_data_fallback()

    if summary in NON_CACHEABLE_FALLBACK_MESSAGES:
        logger.warning(
            "summary generation returned a Groq/tool-loop failure fallback for %s: %r",
            symbol,
            summary,
        )
        return raw_data_fallback()

    backend.upsert_company_summary(symbol, target_date, summary)
    return summary


def _notify_local(target_date: str) -> None:
    backend = get_backend()
    earnings = backend.get_watchlist_earnings_for_date(target_date)
    if not earnings:
        logger.info("no watchlist earnings within 24 hours, skip push")
        return

    banner = _stale_banner(earnings) if _is_stale(earnings) else ""

    by_chat: dict[str, list[dict]] = {}
    for row in earnings:
        by_chat.setdefault(row["chat_id"], []).append(row)

    for chat_id, rows in by_chat.items():
        chat_id = str(chat_id)
        symbols = [row["symbol"] for row in rows]
        pending_symbols = backend.unnotified_symbols(chat_id, symbols, target_date)
        if not pending_symbols:
            continue

        blocks = [
            _company_summary(backend, row, target_date)
            for row in rows
            if row["symbol"] in pending_symbols
        ]
        body = (
            f"{banner}📊 *即將發布財報提醒（{target_date}）*\n\n"
            + "\n\n".join(blocks)
        )

        try:
            send_message(chat_id, body)
        except Exception:
            logger.exception("failed to push to %s", chat_id)
            continue
        backend.mark_notified(chat_id, pending_symbols, target_date)


def _run() -> None:
    target_date = date.today().isoformat()
    _notify_local(target_date)


def _run_sync() -> None:
    """Cloud sync-mode stage (08:00 Asia/Taipei): fetch/write only.

    Each stage is independently try/except'd so one stage's failure doesn't
    block the other. If either failed, re-raise after both have run so the
    invocation counts toward Lambda's `Errors` metric.
    """
    failed = False

    try:
        sync_earnings()
    except Exception:
        logger.exception("sync_earnings stage failed")
        failed = True

    try:
        refresh_news(days_back=1)
    except Exception:
        logger.exception("refresh_news stage failed")
        failed = True

    if failed:
        raise RuntimeError("sync pipeline had at least one failed stage")


def _run_notify() -> None:
    """Cloud notify-mode stage (10:00 Asia/Taipei): read DynamoDB → Groq →
    Telegram push. Does not re-fetch news; the 08:00 sync already did that.

    Same fail-fast-after-completion semantics as `_run_sync()`.
    """
    failed = False

    try:
        _run()
    except Exception:
        logger.exception("notify stage failed")
        failed = True

    try:
        push_daily_summary(refresh=False)
    except Exception:
        logger.exception("push_daily_summary stage failed")
        failed = True

    if failed:
        raise RuntimeError("notify pipeline had at least one failed stage")


@logger.inject_lambda_context
def handler(event: dict, context) -> dict:
    """Cloud entry point: dispatch to the sync or notify stage by
    `event["mode"]`.

    A missing or unknown mode is a fail-fast: this raises before running any
    stage (ADR-0007 §2), so a scheduler misconfiguration is visible as a
    Lambda `Errors` metric rather than silently running the wrong stage at
    the wrong time.
    """
    mode = event.get("mode") if isinstance(event, dict) else None

    if mode == "sync":
        _run_sync()
    elif mode == "notify":
        _run_notify()
    else:
        raise ValueError(f"unknown scheduler mode: {mode!r}; expected 'sync' or 'notify'")

    return {"statusCode": 200}


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "sync":
        failed = sync_earnings()
        print(f"sync done, failed symbols: {failed or '無'}")
    else:
        _run()
