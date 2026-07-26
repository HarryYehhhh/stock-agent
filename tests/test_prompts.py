"""Verify agent.prompts exports the expected non-empty string constants."""

from __future__ import annotations

from agent import prompts


def test_prompt_constants_are_nonempty_strings():
    for name in ("SYSTEM_INSTRUCTION", "SUMMARY_SYSTEM_PROMPT_TEMPLATE", "COMPANY_PROMPT_TEMPLATE"):
        value = getattr(prompts, name)
        assert isinstance(value, str)
        assert value.strip()


def test_summary_system_prompt_daily_wording():
    prompt = prompts.summary_system_prompt(1)

    assert "每日市場摘要" in prompt
    assert "本日發布" in prompt
    assert "今日總結" in prompt
    assert "過去" not in prompt


def test_summary_system_prompt_period_wording():
    prompt = prompts.summary_system_prompt(7)

    assert "過去 7 天市場摘要" in prompt
    assert "過去 7 天發布" in prompt
    assert "本期間總結" in prompt
    assert "每日" not in prompt
    assert "今日總結" not in prompt


# Frozen verbatim copy of the pre-sprint/summary-timewindow SUMMARY_SYSTEM_PROMPT
# constant (git show HEAD~N:src/agent/prompts.py before this change). Production's
# daily cron (push_daily_summary) always calls generate_market_summary(days_back=1)
# and pushes the result unmodified to subscribers, so days_back=1 wording must stay
# byte-for-byte identical to this baseline.
_OLD_SUMMARY_SYSTEM_PROMPT = (
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


def test_summary_system_prompt_days_back_1_is_byte_for_byte_identical_to_pre_sprint_baseline():
    assert prompts.summary_system_prompt(1) == _OLD_SUMMARY_SYSTEM_PROMPT
    assert prompts.summary_system_prompt() == _OLD_SUMMARY_SYSTEM_PROMPT


def test_summary_system_prompt_period_wording_requires_daily_index_label():
    """days_back>1：大盤指數收盤數據是當日漲跌，摘要不得表述成整段期間的變化。"""
    prompt = prompts.summary_system_prompt(7)

    assert "當日" in prompt
    assert "不得表述成整段期間的變化" in prompt


def test_system_instruction_mentions_days_back_cap_and_long_period_mapping():
    """1a：教 LLM days_back 上限 7，且「本月」等更長期間也要傳 7 並在回覆中說明。"""
    assert "days_back 上限為 7" in prompts.SYSTEM_INSTRUCTION
    assert "本月" in prompts.SYSTEM_INSTRUCTION
    assert "僅涵蓋最近 7 天" in prompts.SYSTEM_INSTRUCTION


def test_system_instruction_maps_this_week_to_future_date_range():
    """本週/近期/接下來/最近等時間詞詢問財報行事曆時，date_from/date_to 採未來視角。"""
    assert "本週" in prompts.SYSTEM_INSTRUCTION
    assert "近期" in prompts.SYSTEM_INSTRUCTION
    assert "接下來" in prompts.SYSTEM_INSTRUCTION
    assert "最近" in prompts.SYSTEM_INSTRUCTION
    assert "date_from 傳今天日期，date_to 傳今天加 7 天" in prompts.SYSTEM_INSTRUCTION
    assert "過去或已公布的財報" in prompts.SYSTEM_INSTRUCTION


def test_system_instruction_requires_earnings_hour_label_in_reply():
    """回覆財報行事曆結果時，每筆財報都要標示日期與盤前/盤後/盤中，缺值標「時段未定」。"""
    assert "盤前/盤後/盤中" in prompts.SYSTEM_INSTRUCTION
    assert "bmo/amc/dmh" in prompts.SYSTEM_INSTRUCTION
    assert "時段未定" in prompts.SYSTEM_INSTRUCTION
