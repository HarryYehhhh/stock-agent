"""agent.audit.dump: DynamoDB -> dated markdown snapshots (ADR-0010)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from agent.audit import dump, judge
from agent.knowledge.retrieval import parse_sections, retrieve_scored

DATE = "2026-07-27"


@pytest.fixture(autouse=True)
def _isolated_dirs(monkeypatch, tmp_path):
    """Never write to the real repo snapshots/ or audit/ trees during tests."""
    monkeypatch.setattr(dump, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(dump, "AUDIT_DIR", tmp_path / "audit")
    return tmp_path


def _run_dump(table: str = "all", date_str: str = DATE) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    dump.add_arguments(parser)
    args = parser.parse_args(["--date", date_str, "--table", table])
    return dump.run(args)


def test_dump_earnings_format_and_sort(aws):
    table = aws["ddb"].Table("test-earnings-calendar")
    # Out-of-order inserts to prove output sorting (symbol asc, date asc
    # within symbol) is not just insertion order.
    table.put_item(
        Item={
            "symbol": "MSFT",
            "earning_date": "2026-08-01",
            "extended_hour": "amc",
            "eps_estimate": Decimal("2.5"),
            "revenue_estimate": Decimal("65000000000"),
        }
    )
    table.put_item(
        Item={
            "symbol": "AAPL",
            "earning_date": "2026-08-05",
            "extended_hour": "bmo",
            "eps_estimate": Decimal("1.5"),
            "revenue_estimate": Decimal("94000000000"),
        }
    )
    table.put_item(
        Item={
            "symbol": "AAPL",
            "earning_date": "2026-05-01",
            # no extended_hour -> "時段未定"
            "eps_estimate": Decimal("1.2"),
        }
    )

    assert _run_dump(table="earnings") == 0

    out_path = dump.SNAPSHOTS_DIR / DATE / "earnings.md"
    assert out_path.is_file()
    text = out_path.read_text(encoding="utf-8")

    # Metadata lives before the first "## " heading.
    head = text.split("## ", 1)[0]
    assert "產生時間" in head
    assert "來源表：test-earnings-calendar" in head
    assert "筆數：3" in head

    sections = parse_sections(text, "earnings")
    headings = [s.heading for s in sections]
    assert headings == ["AAPL", "MSFT"]  # symbol alphabetical order

    aapl_section = sections[0]
    # Date ascending within symbol: 2026-05-01 before 2026-08-05.
    idx_may = aapl_section.content.index("2026-05-01")
    idx_aug = aapl_section.content.index("2026-08-05")
    assert idx_may < idx_aug
    assert "時段未定" in aapl_section.content  # missing extended_hour
    assert "盤前" in aapl_section.content
    assert "EPS 預估 $1.50" in aapl_section.content
    assert "營收預估 94.0B" in aapl_section.content
    # Missing revenue_estimate -> field omitted entirely, no stray "None".
    assert "None" not in aapl_section.content
    assert "營收預估" not in aapl_section.content.split("2026-05-01")[1].split("\n")[0]

    msft_section = sections[1]
    assert "盤後" in msft_section.content
    assert "EPS 預估 $2.50" in msft_section.content
    assert "營收預估 65.0B" in msft_section.content


def test_dump_earnings_snapshot_is_retrievable(aws):
    table = aws["ddb"].Table("test-earnings-calendar")
    table.put_item(
        Item={
            "symbol": "NVDA",
            "earning_date": "2026-08-20",
            "extended_hour": "amc",
            "eps_estimate": Decimal("0.8"),
            "revenue_estimate": Decimal("30000000000"),
        }
    )

    assert _run_dump(table="earnings") == 0

    hits = retrieve_scored("NVDA 財報", top_k=3, source_dir=dump.SNAPSHOTS_DIR / DATE)
    assert hits
    assert hits[0][0].source == "earnings#NVDA"


def test_dump_conversations_groups_and_orders(aws):
    table = aws["ddb"].Table("test-conversations")
    # Out-of-order inserts across two chat_ids.
    table.put_item(
        Item={"chat_id": "222", "timestamp": "2026-07-27T02:00:00+00:00", "role": "assistant", "content": "B2"}
    )
    table.put_item(
        Item={"chat_id": "111", "timestamp": "2026-07-27T01:00:00+00:00", "role": "user", "content": "A1"}
    )
    table.put_item(
        Item={"chat_id": "111", "timestamp": "2026-07-27T00:00:00+00:00", "role": "user", "content": "A0"}
    )
    table.put_item(
        Item={"chat_id": "222", "timestamp": "2026-07-27T01:00:00+00:00", "role": "user", "content": "B1"}
    )

    assert _run_dump(table="conversations") == 0

    out_path = dump.AUDIT_DIR / DATE / "conversations.md"
    assert out_path.is_file()
    text = out_path.read_text(encoding="utf-8")

    assert "筆數：4" in text.split("## ", 1)[0]

    # chat_id grouping order + within-group ascending timestamp order.
    idx_111 = text.index("## chat_id=111")
    idx_222 = text.index("## chat_id=222")
    assert idx_111 < idx_222
    block_111 = text[idx_111:idx_222]
    assert block_111.index("A0") < block_111.index("A1")


def test_dump_creates_missing_directories(aws, tmp_path):
    assert not (dump.SNAPSHOTS_DIR / DATE).exists()
    assert not (dump.AUDIT_DIR / DATE).exists()

    assert _run_dump(table="all") == 0

    assert (dump.SNAPSHOTS_DIR / DATE / "earnings.md").is_file()
    assert (dump.AUDIT_DIR / DATE / "conversations.md").is_file()
    assert (dump.SNAPSHOTS_DIR / DATE / "news.md").is_file()


def _put_news(aws, **overrides):
    item = {
        "headline": overrides.pop("headline"),
        "bucket": "news",
        "published_at": "2026-07-20T00:00:00+00:00",
        "category": "big7",
        "summary": "a summary",
    }
    item.update(overrides)
    if item.get("published_at") is None:
        # DynamoDB rejects an empty-string value for a GSI key attribute;
        # dropping the key entirely models real legacy rows with no
        # published_at at all.
        item.pop("published_at", None)
    aws["ddb"].Table("test-news").put_item(Item=item)


def test_dump_news_format_sections_and_sort(aws):
    # Two AAPL headlines on the same day (headline alpha tiebreak) plus one
    # older AAPL headline (symbol×date sections sorted date descending), a
    # MSFT headline (symbol alpha order after AAPL), an AI-category headline
    # (symbol=None), a headline with no published_at (falls into the "日期
    # 未知" section, sorted last), and a market_summary row spanning two
    # dates. Inserted out of order to prove sort isn't insertion order.
    _put_news(
        aws,
        headline="Zebra headline wins tiebreak last",
        published_at="2026-07-25T09:00:00+00:00",
        symbol="AAPL",
        summary="English summary for zebra.",
    )
    _put_news(
        aws,
        headline="Apple headline wins tiebreak first",
        published_at="2026-07-25T03:00:00+00:00",
        symbol="AAPL",
        summary="English summary for apple, same day as zebra.",
    )
    _put_news(
        aws,
        headline="Older AAPL headline",
        published_at="2026-07-10T00:00:00+00:00",
        symbol="AAPL",
        summary="Should sort into its own section, after 2026-07-25.",
    )
    _put_news(
        aws,
        headline="Undated AAPL headline",
        published_at=None,  # omitted entirely -> falls into 日期未知 section
        symbol="AAPL",
        summary="No published_at -> 日期未知 section, sorted last.",
    )
    _put_news(
        aws,
        headline="MSFT cloud news",
        published_at="2026-07-25T00:00:00+00:00",
        symbol="MSFT",
        summary="MSFT summary.",
    )
    _put_news(
        aws,
        headline="AI chip demand surges",
        published_at="2026-07-24T00:00:00+00:00",
        symbol=None,
        category="ai",
        summary="AI industry summary.",
    )
    aws["ddb"].Table("test-news").put_item(
        Item={
            "headline": "SUMMARY#2026-07-27#1#abc123",
            "bucket": "market_summary",
            "summary": "今日大盤摘要，科技股領漲。",
            "summary_date": "2026-07-27",
            "days_back": 1,
            "created_at": "2026-07-27T00:00:00+00:00",
        }
    )
    aws["ddb"].Table("test-news").put_item(
        Item={
            "headline": "SUMMARY#2026-07-26#1#def456",
            "bucket": "market_summary",
            "summary": "昨日大盤摘要。",
            "summary_date": "2026-07-26",
            "days_back": 1,
            "created_at": "2026-07-26T00:00:00+00:00",
        }
    )

    assert _run_dump(table="news") == 0

    out_path = dump.SNAPSHOTS_DIR / DATE / "news.md"
    assert out_path.is_file()
    text = out_path.read_text(encoding="utf-8")

    head = text.split("## ", 1)[0]
    assert "產生時間" in head
    assert "來源表：test-news" in head
    assert "一般新聞筆數：6" in head
    assert "市場摘要筆數：2" in head
    assert "近 ~14 天" in head
    assert "近 ~2 天" in head

    sections = parse_sections(text, "news")
    headings = [s.heading for s in sections]
    # symbol alphabetical order, date descending within symbol
    # (日期未知 sorts last); AI + market_summary sections follow, date desc.
    assert headings == [
        "AAPL 新聞 2026-07-25",
        "AAPL 新聞 2026-07-10",
        "AAPL 新聞 日期未知",
        "MSFT 新聞 2026-07-25",
        "AI 產業新聞 2026-07-24",
        "市場摘要 2026-07-27",
        "市場摘要 2026-07-26",
    ]

    aapl_jul25 = sections[0]
    # Same-day tiebreak is headline alphabetical: "Apple" before "Zebra".
    idx_apple = aapl_jul25.content.index("Apple headline wins tiebreak first")
    idx_zebra = aapl_jul25.content.index("Zebra headline wins tiebreak last")
    assert idx_apple < idx_zebra
    assert "English summary for apple" in aapl_jul25.content
    assert "English summary for zebra" in aapl_jul25.content

    aapl_jul10 = sections[1]
    assert "Older AAPL headline" in aapl_jul10.content

    aapl_unknown = sections[2]
    assert "Undated AAPL headline" in aapl_unknown.content

    msft = sections[3]
    assert "MSFT cloud news" in msft.content

    ai_section = sections[4]
    assert "AI chip demand surges" in ai_section.content

    summary_jul27 = sections[5]
    assert "近 1 天" in summary_jul27.content
    assert "今日大盤摘要，科技股領漲。" in summary_jul27.content

    summary_jul26 = sections[6]
    assert "昨日大盤摘要。" in summary_jul26.content


def test_dump_news_summary_truncated_at_150_chars(aws):
    long_summary = "x" * 200  # exceeds _NEWS_SUMMARY_CHARS (150)
    short_summary = "y" * 150  # exactly at the limit, left untouched
    _put_news(aws, headline="Long summary headline", symbol="AAPL", summary=long_summary)
    _put_news(aws, headline="Short summary headline", symbol="AAPL", summary=short_summary)

    assert _run_dump(table="news") == 0

    text = (dump.SNAPSHOTS_DIR / DATE / "news.md").read_text(encoding="utf-8")
    assert "x" * 150 + "…（截斷）" in text
    assert "x" * 151 not in text
    assert short_summary in text
    assert short_summary + "…（截斷）" not in text


def test_dump_news_section_stays_within_max_section_chars_at_peak_volume(aws):
    """核心目標的單元佐證: a peak single symbol×day (8 headlines, matching the
    spec's estimated peak news volume) must fit within MAX_SECTION_CHARS --
    i.e. the section is small enough that judge's `_ask_judge` never
    truncates it (full evidence reaches the prompt)."""
    for i in range(8):
        _put_news(
            aws,
            headline=f"AAPL headline {i:02d} product update",
            published_at="2026-07-25T00:00:00+00:00",
            symbol="AAPL",
            summary="A" * 150,  # worst case: every summary hits the truncation cap
        )

    assert _run_dump(table="news") == 0

    text = (dump.SNAPSHOTS_DIR / DATE / "news.md").read_text(encoding="utf-8")
    sections = parse_sections(text, "news")
    aapl_section = next(s for s in sections if s.heading == "AAPL 新聞 2026-07-25")
    assert len(aapl_section.content) <= judge.MAX_SECTION_CHARS


def test_dump_news_unknown_bucket_ignored(aws):
    _put_news(aws, headline="Real news", symbol="AAPL")
    aws["ddb"].Table("test-news").put_item(
        Item={"headline": "mystery row", "bucket": "something-else"}
    )

    assert _run_dump(table="news") == 0

    text = (dump.SNAPSHOTS_DIR / DATE / "news.md").read_text(encoding="utf-8")
    assert "mystery row" not in text
    assert "Real news" in text


def test_dump_news_market_summary_not_mixed_into_general_news(aws):
    _put_news(aws, headline="AAPL headline", symbol="AAPL")
    aws["ddb"].Table("test-news").put_item(
        Item={
            "headline": "SUMMARY#2026-07-27#1#xyz",
            "bucket": "market_summary",
            "summary": "市場摘要獨立內容",
            "summary_date": "2026-07-27",
            "days_back": 1,
        }
    )

    assert _run_dump(table="news") == 0

    text = (dump.SNAPSHOTS_DIR / DATE / "news.md").read_text(encoding="utf-8")
    sections = parse_sections(text, "news")
    aapl_section = next(s for s in sections if s.heading.startswith("AAPL 新聞"))
    summary_section = next(s for s in sections if s.heading.startswith("市場摘要"))
    assert "市場摘要獨立內容" not in aapl_section.content
    assert "AAPL headline" not in summary_section.content


def test_dump_news_empty_table_no_error(aws):
    assert _run_dump(table="news") == 0

    out_path = dump.SNAPSHOTS_DIR / DATE / "news.md"
    assert out_path.is_file()
    text = out_path.read_text(encoding="utf-8")
    assert "近期無資料" in text
    sections = parse_sections(text, "news")
    assert sections == []


def test_dump_news_snapshot_is_retrievable(aws):
    _put_news(
        aws,
        headline="NVDA earnings beat expectations",
        symbol="NVDA",
        summary="NVDA reported strong quarterly results.",
    )

    assert _run_dump(table="news") == 0

    hits = retrieve_scored(
        "NVDA 最近有什麼新聞", top_k=3, source_dir=dump.SNAPSHOTS_DIR / DATE
    )
    assert hits
    assert hits[0][0].source == "news#NVDA 新聞 2026-07-20"


def test_dump_mixed_index_does_not_dilute_news_vs_earnings(aws):
    """N4/正反例: earnings.md + news.md coexist under the same snapshot dir
    (ADR-0011). News queries must hit news#, earnings queries must hit
    earnings#, without either being outscored by the other."""
    aws["ddb"].Table("test-earnings-calendar").put_item(
        Item={
            "symbol": "AAPL",
            "earning_date": "2026-08-05",
            "extended_hour": "bmo",
            "eps_estimate": Decimal("1.5"),
            "revenue_estimate": Decimal("94000000000"),
        }
    )
    _put_news(
        aws,
        headline="Apple unveils new product lineup",
        symbol="AAPL",
        summary="Apple announced several new products this week.",
    )

    assert _run_dump(table="earnings") == 0
    assert _run_dump(table="news") == 0

    source_dir = dump.SNAPSHOTS_DIR / DATE

    # 正: 新聞型 query 首命中為 news#（新粒度：symbol×日期）。
    news_hits = retrieve_scored("AAPL 最近有什麼新聞", top_k=3, source_dir=source_dir)
    assert news_hits
    assert news_hits[0][0].source == "news#AAPL 新聞 2026-07-20"

    # 反: 財報型 query（EPS / 財報日期）首命中為 earnings#。
    earnings_hits = retrieve_scored(
        "AAPL EPS 財報日期是什麼時候", top_k=3, source_dir=source_dir
    )
    assert earnings_hits
    assert earnings_hits[0][0].source == "earnings#AAPL"


def test_dump_scan_failure_reports_error_no_traceback(aws, monkeypatch, capsys):
    def _boom(_table):
        raise RuntimeError("scan exploded")

    monkeypatch.setattr(dump, "_scan_all", _boom)

    assert _run_dump(table="earnings") == 1
    err = capsys.readouterr().err
    assert "DynamoDB scan failed" in err
    assert "scan exploded" in err
