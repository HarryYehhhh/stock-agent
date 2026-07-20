import sqlite3

from agent.db.conversations import build_messages, get_history, save_message
from agent.db.dedup import claim_update_id
from agent.db.subscribers import get_all_active, subscribe, unsubscribe


def test_local_history_ordering_limit_and_build_messages(local_store):
    for idx in range(5):
        save_message("c1", "user", f"msg-{idx}")

    history = get_history("c1", limit=3)

    assert [item["content"] for item in history] == ["msg-2", "msg-3", "msg-4"]
    assert build_messages("c1")[-1] == {"role": "user", "content": "msg-4"}


def test_local_history_isolated_per_chat(local_store):
    save_message("c1", "user", "a")
    save_message("c2", "assistant", "b")

    assert build_messages("c1") == [{"role": "user", "content": "a"}]
    assert build_messages("c2") == [{"role": "assistant", "content": "b"}]


def test_local_subscriber_active_filtering(local_store):
    subscribe("c1")
    subscribe("c2")
    unsubscribe("c1")

    assert get_all_active() == ["c2"]


def test_local_dedup_first_claim_wins(local_store):
    assert claim_update_id(123) is True
    assert claim_update_id(123) is False

    with sqlite3.connect(local_store) as conn:
        rows = conn.execute("SELECT update_id FROM processed_updates").fetchall()
    assert rows == [(123,)]
