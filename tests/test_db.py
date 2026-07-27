from agent.db.conversations import HISTORY_LIMIT, build_messages, save_message
from agent.db.subscribers import get_all_active, subscribe, unsubscribe


def test_save_and_query_history(aws):
    save_message("c1", "user", "你好")
    save_message("c1", "assistant", "您好")
    save_message("c1", "user", "AAPL 怎樣")

    history = build_messages("c1")
    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    assert history[-1]["content"] == "AAPL 怎樣"


def test_history_isolated_per_chat(aws):
    save_message("c1", "user", "a")
    save_message("c2", "user", "b")
    assert build_messages("c1") == [{"role": "user", "content": "a"}]
    assert build_messages("c2") == [{"role": "user", "content": "b"}]


def test_history_char_budget_drops_oldest_messages(aws):
    # 每則 2500 字元，四則共 10000 字元，超過 6000 字元的預算，
    # 應從最舊的開始整則丟棄，直到剩下的訊息總量 <= 6000。
    for i in range(4):
        save_message("c1", "user", f"msg{i}-" + "x" * 2496)

    history = build_messages("c1")
    assert [m["content"].split("-")[0] for m in history] == ["msg2", "msg3"]


def test_history_limit_still_caps_at_ten_messages(aws):
    for i in range(HISTORY_LIMIT + 5):
        save_message("c1", "user", f"m{i}")

    history = build_messages("c1")
    assert len(history) == HISTORY_LIMIT
    assert history[-1]["content"] == f"m{HISTORY_LIMIT + 4}"


def test_history_empty_returns_empty_list(aws):
    assert build_messages("no-such-chat") == []


def test_history_single_oversized_message_is_kept(aws):
    save_message("c1", "user", "short")
    save_message("c1", "user", "y" * 7000)

    history = build_messages("c1")
    assert len(history) == 1
    assert history[0]["content"] == "y" * 7000


def test_subscribe_unsubscribe_flow(aws):
    subscribe("c1")
    subscribe("c2")
    assert sorted(get_all_active()) == ["c1", "c2"]

    unsubscribe("c1")
    assert get_all_active() == ["c2"]
