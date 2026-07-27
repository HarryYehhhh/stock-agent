"""agent.knowledge.retrieval: tokenizer, section parsing, BM25 retrieval.

Stdlib-only module: no AWS/network involved, no mocking needed.
"""

from __future__ import annotations

from agent.knowledge import retrieval
from agent.knowledge.retrieval import Section, parse_sections, retrieve, retrieve_scored, tokenize

# --- tokenizer ---


def test_tokenize_ascii_word_run():
    assert tokenize("EPS beat") == ["eps", "beat"]


def test_tokenize_lowercases():
    assert tokenize("DJIA") == ["djia"]


def test_tokenize_cjk_bigram():
    assert tokenize("道瓊怎麼查") == ["道瓊", "瓊怎", "怎麼", "麼查"]


def test_tokenize_cjk_single_char_run_is_unigram():
    # "查" surrounded by ascii separators is a length-1 CJK run.
    assert tokenize("a查b") == ["a", "查", "b"]


def test_tokenize_mixed_chinese_english():
    assert tokenize("指數 ETF") == ["指數", "etf"]


def test_tokenize_drops_punctuation_and_whitespace():
    assert tokenize("EPS beat/miss, guidance!") == ["eps", "beat", "miss", "guidance"]


def test_tokenize_empty_string():
    assert tokenize("") == []


# --- section parsing ---

SAMPLE_MD = """# 文件標題

前言不應該被切成 section。

## 第一個 heading

第一段內容。

### 子標題

子標題內容應併入第一個 section。

## 第二個 heading

第二段內容。
"""


def test_parse_sections_splits_on_level2_heading():
    sections = parse_sections(SAMPLE_MD, "sample")
    assert [s.heading for s in sections] == ["第一個 heading", "第二個 heading"]


def test_parse_sections_source_format():
    sections = parse_sections(SAMPLE_MD, "sample")
    assert sections[0].source == "sample#第一個 heading"
    assert sections[1].source == "sample#第二個 heading"


def test_parse_sections_ignores_preamble_before_first_h2():
    sections = parse_sections(SAMPLE_MD, "sample")
    combined = " ".join(s.content for s in sections)
    assert "前言不應該被切成 section" not in combined


def test_parse_sections_folds_h3_into_parent():
    sections = parse_sections(SAMPLE_MD, "sample")
    assert "子標題內容應併入第一個 section" in sections[0].content
    assert len(sections) == 2


def test_parse_sections_no_h2_returns_empty():
    assert parse_sections("# 只有標題\n\n沒有 level-2 heading。", "sample") == []


# --- BM25 retrieval (against real knowledge/ seed files) ---


def test_retrieve_dow_jones_query_hits_indices_section_with_dia_and_source():
    results = retrieve("道瓊怎麼查")
    assert results, "expected at least one hit"
    top = results[0]
    assert top.source.startswith("indices#")
    assert "怎麼查" in top.heading
    assert "DIA" in top.content


def test_retrieve_earnings_term_query_hits_terms_section():
    results = retrieve("amc 是什麼")
    assert results
    assert any(r.source.startswith("earnings-terms#") for r in results)


def test_retrieve_eps_beat_query_hits_terms_section():
    results = retrieve("EPS beat")
    assert results
    assert results[0].source.startswith("earnings-terms#")


def test_retrieve_irrelevant_query_returns_empty():
    assert retrieve("今天天氣如何") == []


def test_retrieve_top_k_limits_result_count():
    results = retrieve("道瓊怎麼查", top_k=1)
    assert len(results) == 1


def test_retrieve_returns_section_instances():
    results = retrieve("道瓊怎麼查", top_k=1)
    assert isinstance(results[0], Section)


# --- index sanity over the real knowledge/ seed files ---


def test_seed_files_each_parse_to_at_least_one_section():
    index = retrieval._get_index(retrieval._KNOWLEDGE_DIR)
    sources_by_file: dict[str, int] = {}
    for section in index.sections:
        stem = section.source.split("#", 1)[0]
        sources_by_file[stem] = sources_by_file.get(stem, 0) + 1
    assert sources_by_file.get("indices", 0) >= 1
    assert sources_by_file.get("earnings-terms", 0) >= 1
    assert sources_by_file.get("analysis-frameworks", 0) >= 1


def test_seed_sections_have_nonempty_heading_and_content():
    index = retrieval._get_index(retrieval._KNOWLEDGE_DIR)
    for section in index.sections:
        assert section.heading.strip()
        assert section.content.strip()


# --- retrieve_scored (ADR-0009: source_dir parameterization) ---


def test_retrieve_scored_returns_section_score_pairs():
    results = retrieve_scored("道瓊怎麼查", top_k=1)
    assert len(results) == 1
    section, score = results[0]
    assert isinstance(section, Section)
    assert isinstance(score, float)
    assert score > 0


def test_retrieve_scored_scores_are_descending():
    results = retrieve_scored("EPS beat", top_k=5)
    scores = [score for _, score in results]
    assert scores == sorted(scores, reverse=True)


def test_retrieve_matches_retrieve_scored_default_source():
    """retrieve()'s behavior/default index (knowledge/*.md) is unchanged:
    it must equal retrieve_scored() against _KNOWLEDGE_DIR with scores dropped."""
    query = "道瓊怎麼查"
    assert retrieve(query, top_k=2) == [
        section for section, _ in retrieve_scored(query, top_k=2, source_dir=retrieval._KNOWLEDGE_DIR)
    ]


def test_retrieve_scored_irrelevant_query_returns_empty():
    assert retrieve_scored("今天天氣如何", source_dir=retrieval._KNOWLEDGE_DIR) == []


# --- two-index isolation (ADR-0009) ---


def test_kb_index_never_contains_playbook_file_stems():
    index = retrieval._get_index(retrieval._KNOWLEDGE_DIR)
    stems = {s.source.split("#", 1)[0] for s in index.sections}
    assert stems.isdisjoint({"market-trends", "earnings", "stock-news"})


def test_playbook_index_only_contains_playbook_file_stems():
    index = retrieval._get_index(retrieval.PLAYBOOKS_DIR)
    stems = {s.source.split("#", 1)[0] for s in index.sections}
    assert stems
    assert stems <= {"market-trends", "earnings", "stock-news"}


def test_playbook_retrieval_never_returns_kb_headings():
    results = retrieve_scored("道瓊怎麼查", top_k=5, source_dir=retrieval.PLAYBOOKS_DIR)
    stems = {section.source.split("#", 1)[0] for section, _ in results}
    assert stems.isdisjoint({"indices", "earnings-terms", "analysis-frameworks"})


def test_kb_retrieval_never_returns_playbook_headings():
    results = retrieve_scored("本週有什麼公司發布財報", top_k=5, source_dir=retrieval._KNOWLEDGE_DIR)
    stems = {section.source.split("#", 1)[0] for section, _ in results}
    assert stems.isdisjoint({"market-trends", "earnings", "stock-news"})


# --- playbook file sanity (contract deliverable #1) ---

_PLAYBOOK_SUBSECTIONS = ("觸發語", "工具指引", "回覆要求", "禁止")


def test_each_playbook_file_parses_at_least_one_section():
    index = retrieval._get_index(retrieval.PLAYBOOKS_DIR)
    stems = {s.source.split("#", 1)[0] for s in index.sections}
    assert stems == {"market-trends", "earnings", "stock-news"}


def test_each_playbook_section_has_all_four_subsections():
    index = retrieval._get_index(retrieval.PLAYBOOKS_DIR)
    assert index.sections, "expected at least one playbook section"
    for section in index.sections:
        for marker in _PLAYBOOK_SUBSECTIONS:
            assert marker in section.content, f"{section.source} missing 「{marker}」"


# --- earnings playbook query hits (contract "Tests required") ---


def test_earnings_calendar_query_top1_is_earnings_calendar_section():
    """Raw BM25 top-1 (no MIN_SCORE threshold) must be the earnings calendar
    section. Exclusion of unrelated stock-news sections from the actually
    *injected* set is threshold-dependent and covered at the
    agent._retrieve_playbook_hits level (see test_playbook_injection.py)."""
    results = retrieve_scored("本週有什麼公司發布財報", top_k=2, source_dir=retrieval.PLAYBOOKS_DIR)
    assert results, "expected at least one hit"
    top_section, _ = results[0]
    assert top_section.source == "earnings#財報行事曆時間查詢"


def test_market_trends_query_hits_market_trends_playbook():
    results = retrieve_scored("今天大盤怎樣", top_k=2, source_dir=retrieval.PLAYBOOKS_DIR)
    assert results
    assert results[0][0].source.startswith("market-trends#")


def test_stock_news_query_hits_stock_news_playbook():
    results = retrieve_scored("AAPL 有什麼新聞", top_k=2, source_dir=retrieval.PLAYBOOKS_DIR)
    assert results
    assert results[0][0].source.startswith("stock-news#")
