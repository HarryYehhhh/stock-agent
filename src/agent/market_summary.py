"""Daily market summary: Big 7 tech stocks + AI industry news, digested by Groq.

Flow: fetch raw English news from Finnhub and store it in the local SQLite
store (`news` table), then read the stored news back and ask the model to
synthesize a Traditional Chinese daily briefing (thematic summary, not
translation). Each generated summary is persisted in `market_summaries`
together with the source news ids so model output can be audited against
its inputs.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

from agent.agent import MODEL, _client
from agent.config import finnhub_api_key
from agent.db.backend import get_backend
from agent.db.subscribers import get_all_active
from agent.telegram_client import send_message
from agent.tools._http import client
from agent.tools.quote import get_quote

log = logging.getLogger(__name__)

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

SUMMARY_SYSTEM_PROMPT = (
    "你是美股科技板塊的資深分析師，負責撰寫每日市場摘要。"
    "一律使用繁體中文。"
    "你會收到大盤指數收盤數據，以及一批本日發布的英文新聞"
    "（Big 7 科技股個股新聞與 AI 產業新聞）。"
    "挑選最重要的三則新聞，寫出摘要。"
    "不要逐條翻譯新聞，而是歸納整合：把相同主題的多則新聞合併成一個觀點，"
    "指出事件對個股與 AI 產業的影響，並過濾掉無關緊要的內容。"
    "輸出格式使用 Telegram Markdown（粗體寫成 *text*，不要使用 ** 或 __），結構如下：\n"
    "*📊 Big 7 動態* — 依重要性整理各公司關鍵事件，每家最多 2-3 句\n"
    "*🤖 AI 產業焦點* — 整理 AI 相關趨勢與重大消息\n"
    "*💡 今日總結* — 結合大盤指數漲跌與新聞，2-3 句話做簡短市場分析，"
    "總結整體市場方向與值得關注的重點"
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
            if not headline:
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
        if not headline or not _AI_PATTERN.search(f"{headline} {summary}"):
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

    return get_backend().insert_news(items)


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


def generate_market_summary(days_back: int = 1) -> dict:
    """Read stored news and return the Groq-synthesized daily summary.

    Call refresh_news() first to pull the latest news into the store. The
    generated summary is persisted with its source news ids for auditing.
    """
    news = select_news(days_back)
    indexes = fetch_index_quotes()
    index_block = _format_index_block(indexes) if indexes else ""

    if not news["big7"] and not news["ai"]:
        summary = "本地儲存中沒有這個區間的 Big 7 或 AI 新聞，請先執行新聞抓取。"
        if index_block:
            summary = f"{index_block}\n\n{summary}"
        return {"summary": summary, "news": news, "indexes": indexes}

    index_lines = "\n".join(
        f"- {q['name']} ({q['symbol']}): 收盤 {q['close']}, 漲跌 {q['change_percent']:+.2f}%"
        for q in indexes
    ) or "（大盤數據暫時無法取得）"
    user_content = (
        f"今天日期是 {date.today().isoformat()}。"
        f"以下是大盤收盤數據與過去 {days_back} 天的新聞資料，請產出每日市場摘要：\n\n"
        f"[大盤收盤]\n{index_lines}\n\n"
        f"{_format_news_block(news)}"
    )
    response = _client().chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
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
    news_ids = [it["id"] for it in news["big7"] + news["ai"]]
    summary_id = get_backend().insert_market_summary(summary, news_ids)
    return {"summary": summary, "summary_id": summary_id, "news": news, "indexes": indexes}


def push_daily_summary() -> None:
    """CLI/cron entry: refresh Big 7 / AI news, generate the daily summary,
    then push it to every active subscriber. Skips the push (but still
    refreshes/generates) when there is no news to summarize. A single
    subscriber's send failure does not stop the others."""
    refresh_news(days_back=1)
    result = generate_market_summary(days_back=1)

    if not result["news"]["big7"] and not result["news"]["ai"]:
        log.info("no news to summarize, skip push")
        return

    subscribers = get_all_active()
    if not subscribers:
        log.info("no active subscribers, skip push")
        return

    for chat_id in subscribers:
        try:
            send_message(chat_id, result["summary"])
        except Exception:
            log.exception("failed to push market summary to %s", chat_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    push_daily_summary()
