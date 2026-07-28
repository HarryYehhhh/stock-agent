"""Verify market summary fetch→store→summarize flow without real APIs."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent import market_summary as ms


def _text_response(text: str):
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
    )


@pytest.fixture
def mock_groq(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(ms, "_client", lambda: client)
    return client


@pytest.fixture
def mock_finnhub(monkeypatch):
    def fake_company_news(symbol, date_from, date_to):
        return [
            {
                "headline": "Shared cross-listed story mentions Apple Microsoft Alphabet "
                "Amazon Nvidia Meta and Tesla",
                "summary": "s",
                "datetime": 1_800_000_000,
            },
        ] + [
            {"headline": f"{symbol} story {i}", "summary": "s", "datetime": 1_800_000_000 - i}
            for i in range(5)
        ]

    def fake_tech_news():
        return [
            {"headline": "New AI model launched", "summary": "", "datetime": 1_800_000_000},
            {
                "headline": "Shared cross-listed story mentions Apple Microsoft Alphabet "
                "Amazon Nvidia Meta and Tesla",
                "summary": "AI",
                "datetime": 1_800_000_000,
            },
            {"headline": "Retail sales climb", "summary": "no keyword here", "datetime": 1_800_000_000},
        ]

    monkeypatch.setattr(ms, "_fetch_company_news", fake_company_news)
    monkeypatch.setattr(ms, "_fetch_tech_news", fake_tech_news)


QUOTES = {
    "SPY": {"current_price": 742.09, "change_percent": -0.16},
    "QQQ": {"current_price": 696.06, "change_percent": 0.105},
    "DIA": {"current_price": 517.94, "change_percent": -0.5511},
    "IWM": {"current_price": 292.31, "change_percent": -0.5884},
}


@pytest.fixture
def mock_quotes(monkeypatch):
    monkeypatch.setattr(ms, "get_quote", lambda symbol: dict(QUOTES[symbol]))


# ---------------------------------------------------------------------------
# _ny_today() / _is_trading_day(): US trading-day gate for the daily summary
# ---------------------------------------------------------------------------


def _freeze_ny_now(monkeypatch, wall_clock: datetime) -> None:
    """Pin `datetime.now(tz)` inside market_summary to a fixed wall-clock
    time, regardless of the tz passed in (avoids depending on real
    zoneinfo conversion math for the test's chosen instant)."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return wall_clock.replace(tzinfo=tz) if tz else wall_clock

    monkeypatch.setattr(ms, "datetime", _Frozen)


def test_ny_today_returns_the_frozen_wall_clock_date(monkeypatch):
    _freeze_ny_now(monkeypatch, datetime(2026, 7, 27, 22, 0))

    assert ms._ny_today() == date(2026, 7, 27)


def test_is_trading_day_weekend_is_not_trading(monkeypatch):
    fetch = MagicMock()
    monkeypatch.setattr(ms, "_fetch_market_holidays", fetch)

    assert ms._is_trading_day(date(2026, 7, 25)) is False  # Saturday
    fetch.assert_not_called()


def test_is_trading_day_full_day_holiday_is_not_trading(monkeypatch):
    monkeypatch.setattr(
        ms, "_fetch_market_holidays",
        lambda: [{"eventName": "Christmas", "atDate": "2026-12-24", "tradingHour": "", "postMarket": ""}],
    )

    assert ms._is_trading_day(date(2026, 12, 24)) is False  # Thursday


def test_is_trading_day_half_day_holiday_is_still_trading(monkeypatch):
    monkeypatch.setattr(
        ms, "_fetch_market_holidays",
        lambda: [{"eventName": "Christmas Eve", "atDate": "2026-12-24", "tradingHour": "09:30-13:00", "postMarket": ""}],
    )

    assert ms._is_trading_day(date(2026, 12, 24)) is True  # Thursday


def test_is_trading_day_weekday_no_holiday_is_trading(monkeypatch):
    monkeypatch.setattr(ms, "_fetch_market_holidays", lambda: [])

    assert ms._is_trading_day(date(2026, 7, 27)) is True  # Monday


def test_is_trading_day_fails_open_when_holiday_check_errors(monkeypatch, caplog):
    def boom():
        raise RuntimeError("finnhub down")

    monkeypatch.setattr(ms, "_fetch_market_holidays", boom)

    with caplog.at_level(logging.WARNING):
        assert ms._is_trading_day(date(2026, 7, 27)) is True  # Monday
    assert any("market holiday check failed" in r.message for r in caplog.records)


def test_fetch_market_holidays_calls_finnhub_with_exchange_and_token(monkeypatch):
    resp = MagicMock()
    resp.json.return_value = {
        "data": [{"eventName": "Christmas", "atDate": "2026-12-25", "tradingHour": "", "postMarket": ""}],
    }
    resp.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.get.return_value = resp
    monkeypatch.setattr(ms, "client", lambda: mock_client)
    monkeypatch.setattr(ms, "finnhub_api_key", lambda: "test-key")

    holidays = ms._fetch_market_holidays()

    assert holidays == [{"eventName": "Christmas", "atDate": "2026-12-25", "tradingHour": "", "postMarket": ""}]
    args, kwargs = mock_client.get.call_args
    assert args[0] == "/stock/market-holiday"
    assert kwargs["params"] == {"exchange": "US", "token": "test-key"}


def test_ai_pattern_word_boundary():
    assert ms._AI_PATTERN.search("Nvidia bets big on AI chips")
    assert ms._AI_PATTERN.search("OpenAI releases new model")
    assert not ms._AI_PATTERN.search("The CEO said sales were fair")


def test_refresh_news_stores_and_dedupes(local_store, mock_finnhub):
    inserted = ms.refresh_news(days_back=1)

    # 7 symbols × (1 shared + 5 unique) minus 6 shared dups, plus 1 AI item
    # (shared tech headline deduped, non-AI headline filtered out)
    assert inserted == 7 * 5 + 1 + 1

    with sqlite3.connect(local_store) as conn:
        headlines = [r[0] for r in conn.execute("SELECT headline FROM news").fetchall()]
    assert headlines.count(
        "Shared cross-listed story mentions Apple Microsoft Alphabet Amazon Nvidia Meta and Tesla"
    ) == 1
    assert "Retail sales climb" not in headlines

    # re-running inserts nothing new
    assert ms.refresh_news(days_back=1) == 0


def test_refresh_news_stores_source_and_high_credibility_flag(local_store, monkeypatch):
    """Both ingestion lines (Big 7 company news and AI tech news) must
    preserve Finnhub's `source` field and flag whitelisted outlets."""

    def fake_company_news(symbol, date_from, date_to):
        if symbol != "AAPL":
            return []
        return [
            {
                "headline": "Apple unveils new iPhone lineup",
                "summary": "s",
                "datetime": 1_800_000_000,
                "source": "Reuters",
            },
            {
                "headline": "Apple stock climbs after earnings beat",
                "summary": "s",
                "datetime": 1_800_000_000,
                "source": "Yahoo",
            },
        ]

    def fake_tech_news():
        return [
            {
                "headline": "New AI model launched by big lab",
                "summary": "",
                "datetime": 1_800_000_000,
                "source": "Bloomberg",
            },
            {
                "headline": "AI startup raises funding round",
                "summary": "",
                "datetime": 1_800_000_000,
                "source": "Bloomberg Law",
            },
            {
                "headline": "AI chips in high demand, no source field",
                "summary": "",
                "datetime": 1_800_000_000,
            },
        ]

    monkeypatch.setattr(ms, "_fetch_company_news", fake_company_news)
    monkeypatch.setattr(ms, "_fetch_tech_news", fake_tech_news)

    ms.refresh_news(days_back=1)

    with sqlite3.connect(local_store) as conn:
        conn.row_factory = sqlite3.Row
        rows = {
            r["headline"]: (r["source"], r["high_credibility"])
            for r in conn.execute("SELECT headline, source, high_credibility FROM news")
        }

    assert rows["Apple unveils new iPhone lineup"] == ("Reuters", 1)
    assert rows["Apple stock climbs after earnings beat"] == ("Yahoo", 0)
    assert rows["New AI model launched by big lab"] == ("Bloomberg", 1)
    assert rows["AI startup raises funding round"] == ("Bloomberg Law", 0)
    assert rows["AI chips in high demand, no source field"] == (None, 0)


def test_refresh_news_filters_headlines_not_about_the_symbol(local_store, monkeypatch, caplog):
    """Regression: Finnhub's /company-news is mention-based tagging, so an
    Intel-earnings story that merely mentions Apple as a customer can get
    tagged AAPL. Such headlines must be filtered out before storing, and the
    filtering must be logged for later observation of the miss rate."""

    def fake_company_news(symbol, date_from, date_to):
        if symbol == "AAPL":
            return [
                {"headline": "How AI-Powered Earnings Powered Intel Stock Higher", "summary": "s", "datetime": 1_800_000_000},
                {"headline": "Apple unveils new iPhone lineup", "summary": "s", "datetime": 1_800_000_000},
            ]
        return []

    monkeypatch.setattr(ms, "_fetch_company_news", fake_company_news)
    monkeypatch.setattr(ms, "_fetch_tech_news", lambda: [])

    with caplog.at_level(logging.INFO):
        inserted = ms.refresh_news(days_back=1)

    assert inserted == 1
    with sqlite3.connect(local_store) as conn:
        headlines = [r[0] for r in conn.execute("SELECT headline FROM news").fetchall()]
    assert headlines == ["Apple unveils new iPhone lineup"]
    assert any(
        "filtered as not about AAPL" in r.message and "Intel Stock Higher" in r.message
        for r in caplog.records
    )


def test_refresh_news_survives_fetch_errors(local_store, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("finnhub down")

    monkeypatch.setattr(ms, "_fetch_company_news", boom)
    monkeypatch.setattr(ms, "_fetch_tech_news", boom)

    assert ms.refresh_news() == 0


def test_select_news_caps(local_store, mock_finnhub):
    ms.refresh_news(days_back=1)
    news = ms.select_news(days_back=1)

    assert len(news["ai"]) == 1
    symbols = [n["symbol"] for n in news["big7"]]
    for symbol in ms.BIG7:
        assert symbols.count(symbol) <= ms.MAX_NEWS_PER_SYMBOL


def test_fetch_index_quotes_skips_failures(monkeypatch):
    def flaky_quote(symbol):
        if symbol == "QQQ":
            raise RuntimeError("finnhub down")
        if symbol == "DIA":
            return {"current_price": 0, "change_percent": None}
        return dict(QUOTES[symbol])

    monkeypatch.setattr(ms, "get_quote", flaky_quote)
    quotes = ms.fetch_index_quotes()

    assert [q["symbol"] for q in quotes] == ["SPY", "IWM"]
    assert quotes[0] == {
        "name": "S&P 500", "symbol": "SPY", "close": 742.09, "change_percent": -0.16,
    }


def test_format_index_block():
    block = ms._format_index_block([
        {"name": "S&P 500", "symbol": "SPY", "close": 742.09, "change_percent": -0.16},
        {"name": "Nasdaq 100", "symbol": "QQQ", "close": 696.06, "change_percent": 0.105},
    ])

    assert block.splitlines() == [
        "*📈 大盤收盤指標*（ETF 代理）",
        "🔴 S&P 500 (SPY)：742.09（-0.16%）",
        "🟢 Nasdaq 100 (QQQ)：696.06（+0.10%）",
    ]


def test_compute_date_range_single_day():
    assert ms._compute_date_range([
        {"published_at": "2026-07-25T12:00:00+00:00"},
        {"published_at": "2026-07-25T08:00:00+00:00"},
    ]) == "2026-07-25"


def test_compute_date_range_multi_day():
    assert ms._compute_date_range([
        {"published_at": "2026-07-25T12:00:00+00:00"},
        {"published_at": "2026-07-19T08:00:00+00:00"},
    ]) == "2026-07-19 ~ 2026-07-25"


def test_compute_date_range_no_news():
    assert ms._compute_date_range([]) is None


def test_generate_summary_reads_store_and_persists(
    local_store, mock_finnhub, mock_groq, mock_quotes
):
    ms.refresh_news(days_back=1)
    mock_groq.chat.completions.create.return_value = _text_response("*📊 Big 7 動態* 摘要內容")

    out = ms.generate_market_summary(days_back=1)

    assert out["summary"].startswith("*📈 大盤收盤指標*")
    assert "🔴 S&P 500 (SPY)：742.09（-0.16%）" in out["summary"]
    assert out["summary"].endswith("*📊 Big 7 動態* 摘要內容")
    assert [q["symbol"] for q in out["indexes"]] == ["SPY", "QQQ", "DIA", "IWM"]
    assert out["date_range"] == "2027-01-15"
    messages = mock_groq.chat.completions.create.call_args.kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert "繁體中文" in messages[0]["content"]
    assert "每日市場摘要" in messages[0]["content"]
    assert "[大盤收盤]" in messages[1]["content"]
    assert "S&P 500 (SPY): 收盤 742.09, 漲跌 -0.16%" in messages[1]["content"]
    assert "AAPL story 0" in messages[1]["content"]
    assert "New AI model launched" in messages[1]["content"]
    # days_back=1 must not carry the multi-day coverage note (production's
    # daily cron pushes this text unmodified — see push_daily_summary).
    assert "實際涵蓋日期" not in messages[1]["content"]
    assert "註明這份資料涵蓋的日期區間" not in messages[1]["content"]

    # summary persisted with source news ids
    with sqlite3.connect(local_store) as conn:
        row = conn.execute(
            "SELECT id, summary, news_ids FROM market_summaries"
        ).fetchone()
    assert row[0] == out["summary_id"]
    assert row[1] == out["summary"]
    stored_ids = json.loads(row[2])
    expected_ids = [n["id"] for n in out["news"]["big7"] + out["news"]["ai"]]
    assert stored_ids == expected_ids


def test_generate_summary_days_back_1_user_content_is_byte_for_byte_identical_to_pre_sprint_baseline(
    local_store, mock_finnhub, mock_groq, mock_quotes
):
    """Production's daily cron (push_daily_summary) always calls this with
    days_back=1 and pushes the result unmodified to subscribers, so the
    days_back=1 user_content sent to Groq must stay byte-for-byte identical
    to the pre-sprint/summary-timewindow baseline (no date-range note)."""
    from datetime import date as date_cls

    ms.refresh_news(days_back=1)
    mock_groq.chat.completions.create.return_value = _text_response("摘要內容")

    ms.generate_market_summary(days_back=1)

    messages = mock_groq.chat.completions.create.call_args.kwargs["messages"]
    index_lines = "\n".join(
        f"- {q['name']} ({q['symbol']}): 收盤 {q['close']}, 漲跌 {q['change_percent']:+.2f}%"
        for q in ms.fetch_index_quotes()
    )
    news = ms.select_news(days_back=1)
    expected_user_content = (
        f"今天日期是 {date_cls.today().isoformat()}。"
        f"以下是大盤收盤數據與過去 1 天的新聞資料，請產出每日市場摘要：\n\n"
        f"[大盤收盤]\n{index_lines}\n\n"
        f"{ms._format_news_block(news)}"
    )
    assert messages[1]["content"] == expected_user_content


def test_generate_summary_cache_hit_skips_groq_and_reuses_summary(
    local_store, mock_finnhub, mock_groq, mock_quotes
):
    """Second call same day, same days_back, same selected news: no Groq
    call, same summary text returned (ADR-0006 §3 revision)."""
    ms.refresh_news(days_back=1)
    mock_groq.chat.completions.create.return_value = _text_response("*📊 Big 7 動態* 摘要內容")

    out1 = ms.generate_market_summary(days_back=1)
    out2 = ms.generate_market_summary(days_back=1)

    assert out1["summary"] == out2["summary"]
    mock_groq.chat.completions.create.assert_called_once()


def test_generate_summary_cache_hit_skips_groq_on_dynamodb_backend(
    aws, mock_finnhub, mock_groq, mock_quotes
):
    """Same cache behavior, but on DynamoDBBackend (ADR-0006 §3 revision)."""
    ms.refresh_news(days_back=1)
    mock_groq.chat.completions.create.return_value = _text_response("*📊 Big 7 動態* 摘要內容")

    out1 = ms.generate_market_summary(days_back=1)
    out2 = ms.generate_market_summary(days_back=1)

    assert out1["summary"] == out2["summary"]
    mock_groq.chat.completions.create.assert_called_once()


def test_generate_summary_cache_miss_when_news_set_changes(
    local_store, mock_groq, mock_quotes, monkeypatch
):
    """A different selected-news set (different news_ids_hash) must call
    Groq again even on the same day with the same days_back."""
    extra = {"enabled": False}

    def fake_company_news(symbol, date_from, date_to):
        return [{"headline": f"{symbol} story", "summary": "s", "datetime": 1_800_000_000}]

    def fake_tech_news():
        items = [{"headline": "AI story one", "summary": "AI", "datetime": 1_800_000_000}]
        if extra["enabled"]:
            items.append({"headline": "AI story two", "summary": "AI", "datetime": 1_800_000_000})
        return items

    monkeypatch.setattr(ms, "_fetch_company_news", fake_company_news)
    monkeypatch.setattr(ms, "_fetch_tech_news", fake_tech_news)
    mock_groq.chat.completions.create.side_effect = [
        _text_response("first"),
        _text_response("second"),
    ]

    ms.refresh_news(days_back=1)
    out1 = ms.generate_market_summary(days_back=1)
    assert out1["summary"].endswith("first")

    extra["enabled"] = True
    ms.refresh_news(days_back=1)
    out2 = ms.generate_market_summary(days_back=1)

    assert out2["summary"].endswith("second")
    assert mock_groq.chat.completions.create.call_count == 2


def test_generate_summary_cache_not_shared_across_days_back(
    local_store, mock_finnhub, mock_groq, mock_quotes
):
    """Same news set, different days_back: each days_back gets its own Groq
    call (cache key includes days_back)."""
    mock_groq.chat.completions.create.side_effect = [
        _text_response("summary-1"),
        _text_response("summary-7"),
    ]
    ms.refresh_news(days_back=7)

    out1 = ms.generate_market_summary(days_back=1)
    out7 = ms.generate_market_summary(days_back=7)

    assert out1["summary"].endswith("summary-1")
    assert out7["summary"].endswith("summary-7")
    assert mock_groq.chat.completions.create.call_count == 2


def test_generate_summary_skips_groq_when_store_empty(local_store, mock_groq, mock_quotes):
    out = ms.generate_market_summary()

    assert out["summary"].startswith("*📈 大盤收盤指標*")
    assert "請先執行新聞抓取" in out["summary"]
    assert out["date_range"] is None
    mock_groq.chat.completions.create.assert_not_called()


def test_generate_summary_uses_period_wording_for_multi_day_window(
    local_store, mock_finnhub, mock_groq, mock_quotes
):
    ms.refresh_news(days_back=7)
    mock_groq.chat.completions.create.return_value = _text_response("*📊 Big 7 動態* 摘要內容")

    out = ms.generate_market_summary(days_back=7)

    assert out["date_range"] == "2027-01-15"  # fixture news all share one fake timestamp
    messages = mock_groq.chat.completions.create.call_args.kwargs["messages"]
    assert "過去 7 天市場摘要" in messages[0]["content"]
    assert "每日市場摘要" not in messages[0]["content"]
    assert "過去 7 天市場摘要" in messages[1]["content"]


def test_generate_summary_multi_day_window_annotates_index_as_daily_change(
    local_store, mock_finnhub, mock_groq, mock_quotes
):
    """days_back>1：大盤指數收盤數據其實是當日漲跌（Finnhub 免費版無歷史 K
    線），system prompt 與 user_content 都須要求標註為「當日」，不得表述成
    整段期間的變化。"""
    ms.refresh_news(days_back=7)
    mock_groq.chat.completions.create.return_value = _text_response("*📊 Big 7 動態* 摘要內容")

    ms.generate_market_summary(days_back=7)

    messages = mock_groq.chat.completions.create.call_args.kwargs["messages"]
    assert "當日" in messages[0]["content"]
    assert "不得表述成整段期間的變化" in messages[1]["content"]
    assert "[大盤收盤（當日漲跌）]" in messages[1]["content"]


# ---------------------------------------------------------------------------
# push_daily_summary: CLI/cron entry
# ---------------------------------------------------------------------------


@pytest.fixture
def push_mocks(local_store, mock_finnhub, mock_groq, mock_quotes, monkeypatch):
    from agent.db.subscribers import subscribe

    mock_groq.chat.completions.create.return_value = _text_response("*📊 Big 7 動態* 摘要內容")
    fake_send = MagicMock()
    monkeypatch.setattr(ms, "send_message", fake_send)
    monkeypatch.setattr(ms, "_is_trading_day", lambda ny_date: True)
    subscribe("111")
    subscribe("222")
    return {"send": fake_send}


def test_push_daily_summary_sends_to_all_active_subscribers(push_mocks):
    ms.push_daily_summary()

    assert push_mocks["send"].call_count == 2
    chat_ids = {call.args[0] for call in push_mocks["send"].call_args_list}
    assert chat_ids == {"111", "222"}
    for call in push_mocks["send"].call_args_list:
        assert "*📊 Big 7 動態* 摘要內容" in call.args[1]


def test_push_daily_summary_logs_pushed_count(push_mocks, caplog):
    """The module-level `log` is an aws_lambda_powertools Logger, which
    doesn't write through pytest's `capsys` (its stream handler is bound to
    the original stdout at import time, before capsys patches it). caplog
    still works because it attaches via the stdlib logging handler/propagate
    mechanism, which powertools Logger goes through."""
    with caplog.at_level(logging.INFO):
        ms.push_daily_summary()

    assert any(
        "push_daily_summary: pushed to 2/2 active subscribers" in r.message
        for r in caplog.records
    )


def test_push_daily_summary_skips_send_when_no_news(local_store, mock_groq, mock_quotes, monkeypatch):
    from agent.db.subscribers import subscribe

    fake_send = MagicMock()
    monkeypatch.setattr(ms, "send_message", fake_send)
    monkeypatch.setattr(ms, "_is_trading_day", lambda ny_date: True)
    subscribe("111")

    ms.push_daily_summary()

    fake_send.assert_not_called()
    mock_groq.chat.completions.create.assert_not_called()


def test_push_daily_summary_skips_send_when_no_subscribers(local_store, mock_finnhub, mock_groq, mock_quotes, monkeypatch):
    mock_groq.chat.completions.create.return_value = _text_response("*📊 Big 7 動態* 摘要內容")
    fake_send = MagicMock()
    monkeypatch.setattr(ms, "send_message", fake_send)
    monkeypatch.setattr(ms, "_is_trading_day", lambda ny_date: True)

    ms.push_daily_summary()

    fake_send.assert_not_called()


def test_push_daily_summary_send_failure_does_not_stop_other_subscribers(push_mocks):
    push_mocks["send"].side_effect = [RuntimeError("telegram down"), None]

    ms.push_daily_summary()

    assert push_mocks["send"].call_count == 2


def test_push_daily_summary_refresh_false_skips_news_refetch(push_mocks, monkeypatch):
    ms.refresh_news(days_back=1)  # seed the store, as if the 08:00 sync stage already ran
    fake_refresh = MagicMock()
    monkeypatch.setattr(ms, "refresh_news", fake_refresh)

    ms.push_daily_summary(refresh=False)

    fake_refresh.assert_not_called()
    assert push_mocks["send"].call_count == 2


def test_push_daily_summary_is_idempotent_per_summary_date(push_mocks):
    ms.push_daily_summary()
    assert push_mocks["send"].call_count == 2

    # Same day rerun: every subscriber already notified, no duplicate sends.
    ms.push_daily_summary()
    assert push_mocks["send"].call_count == 2


def test_push_daily_summary_send_failure_is_not_marked_notified_and_retries(push_mocks):
    push_mocks["send"].side_effect = [RuntimeError("telegram down"), None]

    ms.push_daily_summary()
    assert push_mocks["send"].call_count == 2

    # Rerun same day: "222" already notified and skipped; "111" (failed
    # last time, never marked) retries.
    push_mocks["send"].side_effect = None
    ms.push_daily_summary()
    assert push_mocks["send"].call_count == 3
    chat_ids = {call.args[0] for call in push_mocks["send"].call_args_list}
    assert chat_ids == {"111", "222"}


def test_push_daily_summary_end_to_end_on_dynamodb_backend(
    aws, mock_finnhub, mock_groq, mock_quotes, monkeypatch
):
    """Same flow as `push_mocks`, but against DynamoDBBackend (ADR-0006):
    news written to NewsTable, read back via list_news_since, summarized,
    and pushed — proving push_daily_summary/generate_market_summary/
    select_news/refresh_news work unmodified on the dynamodb backend too."""
    from agent.db.backend import get_backend
    from agent.db.subscribers import subscribe

    mock_groq.chat.completions.create.return_value = _text_response(
        "*📊 Big 7 動態* 摘要內容"
    )
    fake_send = MagicMock()
    monkeypatch.setattr(ms, "send_message", fake_send)
    monkeypatch.setattr(ms, "_is_trading_day", lambda ny_date: True)
    subscribe("111")

    ms.push_daily_summary()

    assert fake_send.call_count == 1
    assert "*📊 Big 7 動態* 摘要內容" in fake_send.call_args.args[1]

    stored = get_backend().list_news_since("2000-01-01T00:00:00+00:00")
    assert len(stored) > 0
    assert all(row["id"] == row["headline"] for row in stored)


def test_push_daily_summary_is_idempotent_on_dynamodb_backend(
    aws, mock_finnhub, mock_groq, mock_quotes, monkeypatch
):
    from agent.db.subscribers import subscribe

    mock_groq.chat.completions.create.return_value = _text_response(
        "*📊 Big 7 動態* 摘要內容"
    )
    fake_send = MagicMock()
    monkeypatch.setattr(ms, "send_message", fake_send)
    monkeypatch.setattr(ms, "_is_trading_day", lambda ny_date: True)
    subscribe("111")

    ms.push_daily_summary()
    assert fake_send.call_count == 1

    # Same day rerun: already notified, no duplicate push.
    ms.push_daily_summary()
    assert fake_send.call_count == 1


def test_push_daily_summary_skips_entirely_on_non_trading_day(
    local_store, mock_finnhub, mock_groq, mock_quotes, monkeypatch
):
    from agent.db.subscribers import subscribe

    subscribe("111")
    fake_send = MagicMock()
    fake_refresh = MagicMock()
    monkeypatch.setattr(ms, "send_message", fake_send)
    monkeypatch.setattr(ms, "refresh_news", fake_refresh)
    monkeypatch.setattr(ms, "_is_trading_day", lambda ny_date: False)

    ms.push_daily_summary()

    fake_refresh.assert_not_called()
    fake_send.assert_not_called()
    mock_groq.chat.completions.create.assert_not_called()


def test_push_daily_summary_logs_skip_with_ny_date_not_system_today(local_store, monkeypatch, caplog):
    """Regression: the skip log must report the NY date `_is_trading_day()`
    actually evaluated, not `date.today()` (system/UTC date) — Lambda runs
    in UTC and Taipei 10:00 notify time systematically differs from the NY
    session date, which would otherwise mislead CloudWatch-log-based
    healthchecks."""
    monkeypatch.setattr(ms, "_is_trading_day", lambda ny_date: False)
    monkeypatch.setattr(ms, "_ny_today", lambda: date(2099, 3, 1))

    with caplog.at_level(logging.INFO):
        ms.push_daily_summary()

    assert any(
        "not a US trading day" in r.message and "ny_date=2099-03-01" in r.message
        for r in caplog.records
    )
    assert not any(date.today().isoformat() in r.message for r in caplog.records)
