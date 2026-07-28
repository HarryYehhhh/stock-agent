"""processor_handler /correct routing (ADR-0014): saves a pending correction,
grabs the previous Q/A pair as context, and never touches run_agent."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent import processor_handler
from agent.db.backend import get_backend
from agent.db.conversations import save_message


def _send_update(chat_id: str, text: str) -> None:
    processor_handler._process_update(
        {"message": {"chat": {"id": chat_id}, "text": text}}
    )


@pytest.fixture
def fake_send(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(processor_handler, "send_message", mock)
    return mock


def test_correct_without_content_shows_usage_and_does_not_save(local_store, fake_send):
    _send_update("111", "/correct")

    body = fake_send.call_args.args[1]
    assert "用法" in body
    assert get_backend().list_corrections() == []


def test_correct_with_content_saves_pending_and_confirms(local_store, fake_send):
    _send_update("111", "/correct AAPL 財報應該是 8/1 盤後")

    body = fake_send.call_args.args[1]
    assert "已收到" in body

    corrections = get_backend().list_corrections()
    assert len(corrections) == 1
    correction = corrections[0]
    assert correction["chat_id"] == "111"
    assert correction["correction_text"] == "AAPL 財報應該是 8/1 盤後"
    assert correction["status"] == "pending"
    assert correction["context_question"] is None
    assert correction["context_answer"] is None


def test_correct_captures_previous_qa_as_context(local_store, fake_send):
    save_message("111", "user", "AAPL 財報什麼時候？")
    save_message("111", "assistant", "AAPL 財報是 7/31 盤前")

    _send_update("111", "/correct AAPL 財報應該是 8/1 盤後")

    correction = get_backend().list_corrections()[0]
    assert correction["context_question"] == "AAPL 財報什麼時候？"
    assert correction["context_answer"] == "AAPL 財報是 7/31 盤前"


def test_correct_does_not_call_run_agent(local_store, fake_send, monkeypatch):
    run_agent_mock = MagicMock()
    monkeypatch.setattr(processor_handler, "run_agent", run_agent_mock)

    _send_update("111", "/correct 有問題的回答")

    run_agent_mock.assert_not_called()


def test_welcome_mentions_correct_command(local_store, fake_send):
    _send_update("111", "/start")

    body = fake_send.call_args.args[1]
    assert "/correct" in body
