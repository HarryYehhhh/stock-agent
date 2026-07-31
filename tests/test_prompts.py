"""Verify agent.prompts exports the expected non-empty string constants."""

from __future__ import annotations

from agent import prompts
from agent.knowledge.retrieval import PLAYBOOKS_DIR


def _playbook_text(filename: str) -> str:
    return (PLAYBOOKS_DIR / filename).read_text(encoding="utf-8")


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


# Frozen verbatim copy of the SUMMARY_SYSTEM_PROMPT constant (tech-stock-focus
# sprint: Big 7 -> 科技股焦點, no volume-based selection criterion, wording no
# longer scoped to Big 7 news). Production's daily cron (push_daily_summary)
# always calls generate_market_summary(days_back=1) and pushes the result
# unmodified to subscribers, so days_back=1 wording must stay byte-for-byte
# identical to this baseline.
_OLD_SUMMARY_SYSTEM_PROMPT = (
    "你是美股科技板塊的資深分析師，負責撰寫每日市場摘要。"
    "一律使用繁體中文。"
    "你會收到大盤指數收盤數據、科技股個股報價（收盤價與漲跌幅），"
    "以及一批本日發布的英文新聞"
    "（主要科技股個股新聞與 AI 產業新聞）。"
    "不要逐條翻譯新聞，而是歸納整合：把相同主題的多則新聞合併成一個觀點，"
    "指出事件對個股與 AI 產業的影響，並過濾掉無關緊要的內容。"
    "輸出格式使用 Telegram Markdown（粗體寫成 *text*，不要使用 ** 或 __），結構如下：\n"
    "*📈 科技股焦點* — 只挑當日漲跌幅最顯著的 2-4 檔（優先 |漲跌幅| ≥ 2%），"
    "每檔以「公司名（代號） $價格 +X.X%」開頭，"
    "接著 2-3 句說明價格變動背後的原因，並帶出賣方分析師或機構的論述多空理由。"
    "價格與分析師觀點一律只能引用輸入資料中實際出現的內容，"
    "沒有就不要寫，嚴禁自行推估數字。\n"
    "*🤖 AI 產業焦點* — 只寫當日真正有份量的消息（模型發布、資本支出與算力投資、"
    "晶片供應、監管政策、重大合作或併購），"
    "若當日沒有達到此標準的消息，整段直接略過不輸出，不要為了填版面而寫軟性內容。\n"
    "*💡 今日總結* — 結合大盤指數漲跌、個股表現與新聞，寫 3-4 句市場分析。"
    "需明確點出當日影響風險偏好的總經因素（聯準會利率預期與官員談話、CPI/PCE 通膨數據、"
    "非農就業、10 年期公債殖利率、美元指數、油價、關稅與地緣政治等），"
    "說明這些因素如何放大或抵銷科技股的個別題材，"
    "最後總結整體市場方向與後續值得關注的觀察點。"
    "總經因素若無當日數據可佐證，只陳述已知事實與市場既有定價，不要編造數據。"
)


def test_summary_system_prompt_days_back_1_is_byte_for_byte_identical_to_baseline():
    assert prompts.summary_system_prompt(1) == _OLD_SUMMARY_SYSTEM_PROMPT
    assert prompts.summary_system_prompt() == _OLD_SUMMARY_SYSTEM_PROMPT


def test_summary_system_prompt_period_wording_requires_daily_index_label():
    """days_back>1：大盤指數收盤數據是當日漲跌，摘要不得表述成整段期間的變化。"""
    prompt = prompts.summary_system_prompt(7)

    assert "當日" in prompt
    assert "不得表述成整段期間的變化" in prompt


def test_summary_system_prompt_has_no_volume_selection_criterion():
    """Finnhub 免費版 /quote 沒有成交量欄位，選股條件不得要求「成交量明顯放大」。"""
    prompt = prompts.summary_system_prompt(1)

    assert "成交量" not in prompt
    assert "Big 7" not in prompt


# 以下三則測試對應 ADR-0009 拆遷表 S11/S14/S15/S20：這些規則已從
# SYSTEM_INSTRUCTION MOVE 到 knowledge/playbooks/，斷言改鎖 playbook 檔案內容。


def test_market_trends_playbook_mentions_days_back_cap_and_long_period_mapping():
    """S14/S15：教 LLM days_back 上限 7，且「本月」等更長期間也要傳 7 並在回覆中說明。"""
    content = _playbook_text("market-trends.md")
    assert "上限為 7" in content
    assert "本月" in content
    assert "僅涵蓋最近 7 天" in content


def test_earnings_playbook_maps_this_week_to_future_date_range():
    """S11：本週/近期/接下來/最近等時間詞詢問財報行事曆時，date_from/date_to 採未來視角。"""
    content = _playbook_text("earnings.md")
    assert "本週" in content
    assert "近期" in content
    assert "接下來" in content
    assert "最近" in content
    assert "date_from` 傳今天日期，`date_to` 傳今天加 7 天" in content
    assert "過去或已公布的財報" in content


def test_earnings_playbook_requires_earnings_hour_label_in_reply():
    """S20：回覆財報行事曆結果時，每筆財報都要標示日期與盤前/盤後/盤中，缺值標「時段未定」。"""
    content = _playbook_text("earnings.md")
    assert "盤前/盤後/盤中" in content
    assert "bmo/amc/dmh" in content
    assert "時段未定" in content


def test_earnings_playbook_open_ended_date_query_uses_future_90_day_window():
    """「X 的財報是什麼時候」這類未指定區間的開放式日期查詢，date_from/date_to
    要採未來 90 天視角，且日期一律以 system prompt 的今天日期為準、禁止用過去年份
    （production 429 事件中模型曾誤用 2024 過去年份重試）。"""
    content = _playbook_text("earnings.md")
    assert "財報是什麼時候" in content
    assert "date_from` 傳今天、`date_to` 傳今天加 90 天" in content
    assert "禁止使用過去年份" in content
