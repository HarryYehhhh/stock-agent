from agent.db.conversations import build_messages_for_claude, save_message
from agent.db.subscribers import get_all_active, subscribe, unsubscribe


def test_save_and_query_history(aws):
    save_message("c1", "user", "你好")
    save_message("c1", "assistant", "您好")
    save_message("c1", "user", "AAPL 怎樣")

    history = build_messages_for_claude("c1")
    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    assert history[-1]["content"] == "AAPL 怎樣"


def test_history_isolated_per_chat(aws):
    save_message("c1", "user", "a")
    save_message("c2", "user", "b")
    assert build_messages_for_claude("c1") == [{"role": "user", "content": "a"}]
    assert build_messages_for_claude("c2") == [{"role": "user", "content": "b"}]


def test_subscribe_unsubscribe_flow(aws):
    subscribe("c1")
    subscribe("c2")
    assert sorted(get_all_active()) == ["c1", "c2"]

    unsubscribe("c1")
    assert get_all_active() == ["c2"]
