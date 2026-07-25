"""agent.knowledge.retrieval: tokenizer, section parsing, BM25 retrieval.

Stdlib-only module: no AWS/network involved, no mocking needed.
"""

from __future__ import annotations

from agent.knowledge import retrieval
from agent.knowledge.retrieval import Section, parse_sections, retrieve, tokenize

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
    index = retrieval._get_index()
    sources_by_file: dict[str, int] = {}
    for section in index.sections:
        stem = section.source.split("#", 1)[0]
        sources_by_file[stem] = sources_by_file.get(stem, 0) + 1
    assert sources_by_file.get("indices", 0) >= 1
    assert sources_by_file.get("earnings-terms", 0) >= 1
    assert sources_by_file.get("analysis-frameworks", 0) >= 1


def test_seed_sections_have_nonempty_heading_and_content():
    index = retrieval._get_index()
    for section in index.sections:
        assert section.heading.strip()
        assert section.content.strip()
