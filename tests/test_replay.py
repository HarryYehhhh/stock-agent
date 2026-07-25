"""Same-question replay: processor resends the last stored reply instead of
calling run_agent() when the user repeats their previous question within
processor_handler.REPLAY_WINDOW."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from agent import processor_handler
from agent.db.conversations import get_history, save_message


def _send_update(chat_id: str, text: str) -> None:
    processor_handler._process_update({"message": {"chat": {"id": chat_id}, "text": text}})


def _age_last_message(store_path, chat_id: str, role: str, minutes_ago: int) -> None:
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    with sqlite3.connect(store_path) as conn:
        conn.execute(
            """
            UPDATE conversations SET timestamp = ?
            WHERE chat_id = ? AND role = ?
              AND id = (SELECT MAX(id) FROM conversations WHERE chat_id = ? AND role = ?)
            """,
            (old_ts, chat_id, role, chat_id, role),
        )


@pytest.fixture
def fake_send(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(processor_handler, "send_message", mock)
    return mock


@pytest.fixture
def fake_run_agent(monkeypatch):
    mock = MagicMock(return_value="agent reply")
    monkeypatch.setattr(processor_handler, "run_agent", mock)
    return mock


def test_repeated_question_within_window_replays_without_calling_agent(
    local_store, fake_send, fake_run_agent
):
    save_message("111", "user", "AAPL 財報怎麼樣")
    save_message("111", "assistant", "AAPL 財報優於預期")

    _send_update("111", "AAPL 財報怎麼樣")

    fake_run_agent.assert_not_called()
    fake_send.assert_called_once_with("111", "（與上題相同，以下為稍早的回覆）\nAAPL 財報優於預期")


def test_replay_does_not_write_to_conversation_history(local_store, fake_send, fake_run_agent):
    save_message("111", "user", "AAPL 財報怎麼樣")
    save_message("111", "assistant", "AAPL 財報優於預期")

    _send_update("111", "AAPL 財報怎麼樣")

    assert len(get_history("111", limit=20)) == 2  # unchanged: no new rows written


def test_different_text_does_not_replay(local_store, fake_send, fake_run_agent):
    save_message("111", "user", "AAPL 財報怎麼樣")
    save_message("111", "assistant", "AAPL 財報優於預期")

    _send_update("111", "NVDA 財報怎麼樣")

    fake_run_agent.assert_called_once()


def test_question_older_than_ten_minutes_does_not_replay(local_store, fake_send, fake_run_agent):
    save_message("111", "user", "AAPL 財報怎麼樣")
    save_message("111", "assistant", "AAPL 財報優於預期")
    _age_last_message(local_store, "111", "user", minutes_ago=11)

    _send_update("111", "AAPL 財報怎麼樣")

    fake_run_agent.assert_called_once()


def test_question_within_ten_minutes_boundary_still_replays(
    local_store, fake_send, fake_run_agent
):
    save_message("111", "user", "AAPL 財報怎麼樣")
    save_message("111", "assistant", "AAPL 財報優於預期")
    _age_last_message(local_store, "111", "user", minutes_ago=9)

    _send_update("111", "AAPL 財報怎麼樣")

    fake_run_agent.assert_not_called()


def test_no_prior_history_does_not_replay(local_store, fake_send, fake_run_agent):
    _send_update("111", "AAPL 財報怎麼樣")

    fake_run_agent.assert_called_once()
