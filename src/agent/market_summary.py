"""Daily market summary: Big 7 tech stocks + AI industry news, digested by Groq.

Flow: fetch raw English news from Finnhub and store it in the local SQLite
store (`news` table), then read the stored news back and ask the model to
synthesize a Traditional Chinese daily briefing (thematic summary, not
translation). Each generated summary is persisted in `market_summaries`
together with the source news ids so model output can be audited against
its inputs, and also serves as a cache keyed by (summary_date, days_back,
news_ids_hash): a repeat call with the same day's date, days_back and
selected news set returns the stored summary instead of calling Groq again
(ADR-0006 §3 revision, 2026-07-26).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aws_lambda_powertools import Logger

from agent.agent import MODEL, _client
from agent.config import finnhub_api_key
from agent.db.backend import get_backend
from agent.db.subscribers import get_all_active
from agent.prompts import summary_system_prompt
from agent.telegram_client import send_message
from agent.tools._http import client
from agent.tools.news import is_headline_relevant, is_junk_headline
from agent.tools.quote import get_quote

log = Logger()

BIG7 = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]

# Finnhub free tier has no index quotes (^GSPC etc. need a paid plan),
# so we track the standard ETF proxies instead.
INDEXES = [
    ("S&P 500", "SPY"),
    ("Nasdaq 100", "QQQ"),
    ("Dow Jones", "DIA"),
    ("Russell 2000", "IWM"),
]

MAX_STORE_PER_SYMBOL = 10
MAX_STORE_AI = 15
MAX_NEWS_PER_SYMBOL = 3
MAX_AI_NEWS = 8
MAX_SUMMARY_CHARS = 300

_AI_PATTERN = re.compile(
    r"\bAI\b|artificial intelligence|machine learning|large language model"
    r"|\bLLM\b|OpenAI|ChatGPT|Anthropic|Claude|\bGPT\b|Gemini|generative|copilot",
    re.IGNORECASE,
)


def _fetch_company_news(symbol: str, date_from: str, date_to: str) -> list[dict]:
    r = client().get(
        "/company-news",
        params={"symbol": symbol, "from": date_from, "to": date_to, "token": finnhub_api_key()},
    )
    r.raise_for_status()
    return r.json()


def _fetch_tech_news() -> list[dict]:
    r = client().get(
        "/news",
        params={"category": "technology", "token": finnhub_api_key()},
    )
    r.raise_for_status()
    return r.json()


def _published_at(item: dict) -> str:
    ts = item.get("datetime")
    if ts:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _date_from(days_back: int) -> str:
    days_back = max(1, min(7, days_back))
    return (date.today() - timedelta(days=days_back)).isoformat()


def _fetch_market_holidays() -> list[dict]:
    r = client().get(
        "/stock/market-holiday",
        params={"exchange": "US", "token": finnhub_api_key()},
    )
    r.raise_for_status()
    return r.json().get("data", [])


def _ny_today() -> date:
    """Current New York calendar date. Notify time is 10:00 Asia/Taipei,
    i.e. 22:00 the prior US Eastern day — right after that session's close —
    so this identifies the session `_is_trading_day()` should check."""
    return datetime.now(ZoneInfo("America/New_York")).date()


def _is_trading_day(ny_date: date) -> bool:
    """Whether the US market session on `ny_date` (see `_ny_today()`) was a
    trading day. Weekends are always closed; a full-day Finnhub holiday
    (empty `tradingHour`) is closed too, but a half-day holiday (non-empty
    `tradingHour`) still trades. Finnhub failures fail open (treated as a
    trading day) so a flaky holiday check never silently drops the daily
    push."""
    if ny_date.weekday() >= 5:
        return False
    try:
        holidays = _fetch_market_holidays()
    except Exception as e:
        log.warning("market holiday check failed, treating as trading day: %s", e)
        return True
    iso = ny_date.isoformat()
    return not any(h.get("atDate") == iso and not h.get("tradingHour") for h in holidays)


def refresh_news(days_back: int = 1) -> int:
    """Fetch Big 7 company news and AI-related tech news from Finnhub into
    the local store. Returns the number of newly inserted rows (duplicates
    by headline are ignored)."""
    date_from = _date_from(days_back)
    date_to = date.today().isoformat()

    items: list[dict] = []
    for symbol in BIG7:
        try:
            fetched = _fetch_company_news(symbol, date_from, date_to)
        except Exception as e:
            log.warning("company news fetch failed for %s: %s", symbol, e)
            continue
        for it in fetched[:MAX_STORE_PER_SYMBOL]:
            headline = (it.get("headline") or "").strip()
            if not headline or is_junk_headline(headline):
                continue
            if not is_headline_relevant(symbol, headline):
                log.info("headline filtered as not about %s: %s", symbol, headline)
                continue
            items.append({
                "category": "big7",
                "symbol": symbol,
                "headline": headline,
                "summary": (it.get("summary") or "")[:MAX_SUMMARY_CHARS],
                "url": it.get("url"),
                "published_at": _published_at(it),
            })

    try:
        tech_items = _fetch_tech_news()
    except Exception as e:
        log.warning("tech news fetch failed: %s", e)
        tech_items = []
    picked = 0
    for it in tech_items:
        headline = (it.get("headline") or "").strip()
        summary = it.get("summary") or ""
        if not headline or is_junk_headline(headline):
            continue
        if not _AI_PATTERN.search(f"{headline} {summary}"):
            continue
        items.append({
            "category": "ai",
            "symbol": None,
            "headline": headline,
            "summary": summary[:MAX_SUMMARY_CHARS],
            "url": it.get("url"),
            "published_at": _published_at(it),
        })
        picked += 1
        if picked >= MAX_STORE_AI:
            break

    inserted = get_backend().insert_news(items)
    log.info("refresh_news: inserted %d new rows (days_back=%d)", inserted, days_back)
    return inserted


def list_stored_news(days_back: int = 1) -> list[dict]:
    """Stored news in the requested window, newest first (for inspection)."""
    return get_backend().list_news_since(_date_from(days_back))


def select_news(days_back: int = 1) -> dict:
    """Pick the news rows to feed the model: newest MAX_NEWS_PER_SYMBOL per
    Big 7 symbol plus newest MAX_AI_NEWS AI items."""
    rows = list_stored_news(days_back)
    per_symbol: dict[str, int] = {}
    big7: list[dict] = []
    ai: list[dict] = []
    for row in rows:
        if row["category"] == "big7":
            count = per_symbol.get(row["symbol"], 0)
            if count < MAX_NEWS_PER_SYMBOL:
                per_symbol[row["symbol"]] = count + 1
                big7.append(row)
        elif row["category"] == "ai" and len(ai) < MAX_AI_NEWS:
            ai.append(row)
    return {"big7": big7, "ai": ai}


def _compute_date_range(news_items: list[dict]) -> str | None:
    """Actual coverage of the selected news' `published_at` dates, as a
    single date (e.g. "2026-07-25") or a "from ~ to" range. None if there
    is no news."""
    dates = sorted({item["published_at"][:10] for item in news_items if item.get("published_at")})
    if not dates:
        return None
    if dates[0] == dates[-1]:
        return dates[0]
    return f"{dates[0]} ~ {dates[-1]}"


def fetch_index_quotes() -> list[dict]:
    """Latest close and percent change for the major index ETF proxies.
    Symbols that fail to fetch are skipped."""
    quotes = []
    for name, symbol in INDEXES:
        try:
            q = get_quote(symbol)
        except Exception as e:
            log.warning("index quote fetch failed for %s: %s", symbol, e)
            continue
        if not q.get("current_price"):
            continue
        quotes.append({
            "name": name,
            "symbol": symbol,
            "close": q["current_price"],
            "change_percent": q["change_percent"],
        })
    return quotes


def _format_index_block(quotes: list[dict]) -> str:
    lines = ["*📈 大盤收盤指標*（ETF 代理）"]
    for q in quotes:
        emoji = "🔴" if q["change_percent"] < 0 else "🟢"
        lines.append(
            f"{emoji} {q['name']} ({q['symbol']})："
            f"{q['close']:,.2f}（{q['change_percent']:+.2f}%）"
        )
    return "\n".join(lines)


def _format_news_block(news: dict) -> str:
    lines = ["[Big 7 個股新聞]"]
    for it in news["big7"]:
        lines.append(f"- ({it['symbol']}) {it['headline']}: {it['summary']}")
    lines.append("\n[AI 產業新聞]")
    for it in news["ai"]:
        lines.append(f"- {it['headline']}: {it['summary']}")
    return "\n".join(lines)


def _news_ids_hash(news_ids: list) -> str:
    """Stable hash of a news id set, tolerant of both sqlite's int ids and
    DynamoDB's headline-string ids."""
    ids_str = sorted(str(news_id) for news_id in news_ids)
    return hashlib.sha256(json.dumps(ids_str).encode()).hexdigest()[:16]


def generate_market_summary(days_back: int = 1) -> dict:
    """Read stored news and return the Groq-synthesized daily summary.

    Call refresh_news() first to pull the latest news into the store. The
    generated summary is persisted with its source news ids for auditing and
    cached by (summary_date, days_back, news_ids_hash): a repeat call with
    the same day's date, days_back and selected news set returns the cached
    summary without calling Groq again (ADR-0006 §3 revision).
    """
    news = select_news(days_back)
    selected_news = news["big7"] + news["ai"]
    date_range = _compute_date_range(selected_news)

    if not news["big7"] and not news["ai"]:
        indexes = fetch_index_quotes()
        index_block = _format_index_block(indexes) if indexes else ""
        summary = "本地儲存中沒有這個區間的 Big 7 或 AI 新聞，請先執行新聞抓取。"
        if index_block:
            summary = f"{index_block}\n\n{summary}"
        return {"summary": summary, "news": news, "indexes": indexes, "date_range": date_range}

    summary_date = date.today().isoformat()
    news_ids = [it["id"] for it in selected_news]
    news_ids_hash = _news_ids_hash(news_ids)
    backend = get_backend()
    cached = backend.get_cached_market_summary(summary_date, days_back, news_ids_hash)
    if cached is not None:
        log.info(
            "market summary cache hit (date=%s days_back=%d hash=%s), skipping Groq call",
            summary_date, days_back, news_ids_hash,
        )
        return {
            "summary": cached["summary"],
            "summary_id": cached.get("id"),
            "news": news,
            "indexes": [],
            "date_range": date_range,
        }

    indexes = fetch_index_quotes()
    index_block = _format_index_block(indexes) if indexes else ""
    index_lines = "\n".join(
        f"- {q['name']} ({q['symbol']}): 收盤 {q['close']}, 漲跌 {q['change_percent']:+.2f}%"
        for q in indexes
    ) or "（大盤數據暫時無法取得）"
    # days_back<=1 keeps the original user_content verbatim (production's
    # daily cron always calls this with days_back=1 via push_daily_summary
    # and pushes the result unmodified to subscribers); days_back>1 adds an
    # explicit coverage note so the model states the actual date range.
    if days_back <= 1:
        period_desc = "每日"
        coverage_clause = ""
        index_label = "[大盤收盤]"
    else:
        period_desc = f"過去 {days_back} 天"
        coverage_clause = (
            f"（實際涵蓋日期：{date_range}），並在摘要中註明這份資料涵蓋的日期區間"
            "；大盤收盤數據是當日漲跌幅，不是這段期間的漲跌幅，摘要中提到指數數字時"
            "須標註為「當日」，不得表述成整段期間的變化"
        )
        index_label = "[大盤收盤（當日漲跌）]"
    user_content = (
        f"今天日期是 {date.today().isoformat()}。"
        f"以下是大盤收盤數據與過去 {days_back} 天的新聞資料{coverage_clause}，"
        f"請產出{period_desc}市場摘要：\n\n"
        f"{index_label}\n{index_lines}\n\n"
        f"{_format_news_block(news)}"
    )
    response = _client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": summary_system_prompt(days_back)},
            {"role": "user", "content": user_content},
        ],
        temperature=0.5,
    )
    usage = getattr(response, "usage", None)
    if usage:
        log.info(
            "groq usage (market summary): input=%s output=%s",
            getattr(usage, "prompt_tokens", 0),
            getattr(usage, "completion_tokens", 0),
        )
    summary = (response.choices[0].message.content or "").strip()
    if index_block:
        summary = f"{index_block}\n\n{summary}"
    summary_id = backend.insert_market_summary(
        summary, news_ids, summary_date, days_back, news_ids_hash
    )
    return {
        "summary": summary,
        "summary_id": summary_id,
        "news": news,
        "indexes": indexes,
        "date_range": date_range,
    }


def push_daily_summary(refresh: bool = True) -> None:
    """CLI/cron entry: optionally refresh Big 7 / AI news, generate the daily
    summary, then push it to every active subscriber not already notified for
    today. Skips the push (but still generates) when there is no news to
    summarize. A single subscriber's send failure does not stop the others
    and does not mark that subscriber notified, so a retry can resend to
    them without re-sending to already-notified subscribers (ADR-0007 §4).

    `refresh=False` skips `refresh_news` (used by the cloud notify-mode
    stage, which runs after a separate sync-mode stage already refreshed the
    news); the default `refresh=True` keeps local `make daily-summary`
    behavior unchanged.

    Skips entirely (no refresh, no generation, no push) on days the US
    market didn't trade — see `_is_trading_day()`."""
    ny_date = _ny_today()
    if not _is_trading_day(ny_date):
        log.info("not a US trading day, skip market summary (ny_date=%s)", ny_date.isoformat())
        return
    if refresh:
        refresh_news(days_back=1)
    result = generate_market_summary(days_back=1)

    summary_date = date.today().isoformat()
    if not result["news"]["big7"] and not result["news"]["ai"]:
        log.info("no news to summarize, skip push (date=%s)", summary_date)
        return

    subscribers = get_all_active()
    if not subscribers:
        log.info("no active subscribers, skip push (date=%s)", summary_date)
        return

    backend = get_backend()
    sent = 0
    for chat_id in subscribers:
        if backend.summary_notified(chat_id, summary_date):
            continue
        try:
            send_message(chat_id, result["summary"])
        except Exception:
            log.exception("failed to push market summary to %s", chat_id)
            continue
        backend.mark_summary_notified(chat_id, summary_date)
        sent += 1
    log.info("push_daily_summary: pushed to %d/%d active subscribers", sent, len(subscribers))


if __name__ == "__main__":
    push_daily_summary()
