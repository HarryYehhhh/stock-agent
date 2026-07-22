"""Processor handler command routing tests: /watchlist, /watch, /unwatch, /subscribe seed."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent import processor_handler


def _send_update(chat_id: str, text: str) -> None:
    processor_handler._process_update(
        {"message": {"chat": {"id": chat_id}, "text": text}}
    )


@pytest.fixture
def fake_send(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(processor_handler, "send_message", mock)
    return mock


def test_welcome_mentions_watchlist_commands(local_store, fake_send):
    _send_update("111", "/start")

    body = fake_send.call_args.args[1]
    assert "/watchlist" in body
    assert "/watch" in body
    assert "/unwatch" in body


def test_watchlist_empty_gives_usage_hint(local_store, fake_send):
    _send_update("111", "/watchlist")

    body = fake_send.call_args.args[1]
    assert "/watch" in body


def test_watch_then_appears_in_watchlist_with_uppercase_symbol(local_store, fake_send):
    _send_update("111", "/watch aapl")
    assert "AAPL" in fake_send.call_args.args[1]

    _send_update("111", "/watchlist")
    assert "AAPL" in fake_send.call_args.args[1]


def test_watch_duplicate_is_non_error_reply(local_store, fake_send):
    _send_update("111", "/watch AAPL")
    _send_update("111", "/watch AAPL")

    body = fake_send.call_args.args[1]
    assert "已經" in body


def test_watch_missing_argument_gives_usage(local_store, fake_send):
    _send_update("111", "/watch")

    body = fake_send.call_args.args[1]
    assert "用法" in body


def test_watch_invalid_symbol_special_chars_gives_usage(local_store, fake_send):
    _send_update("111", "/watch $$$weird!!")

    body = fake_send.call_args.args[1]
    assert "用法" in body


def test_watch_symbol_too_long_gives_usage(local_store, fake_send):
    _send_update("111", "/watch " + "A" * 11)

    body = fake_send.call_args.args[1]
    assert "用法" in body


def test_watch_multi_word_argument_gives_usage(local_store, fake_send):
    _send_update("111", "/watch AAPL MSFT")

    body = fake_send.call_args.args[1]
    assert "用法" in body


def test_watch_accepts_symbol_with_dot_and_dash(local_store, fake_send):
    _send_update("111", "/watch brk.b")
    assert "BRK.B" in fake_send.call_args.args[1]

    _send_update("111", "/watch bf-b")
    assert "BF-B" in fake_send.call_args.args[1]


def test_unwatch_invalid_symbol_gives_usage(local_store, fake_send):
    _send_update("111", "/unwatch $$$weird!!")

    body = fake_send.call_args.args[1]
    assert "用法" in body


def test_unwatch_removes_symbol_from_watchlist(local_store, fake_send):
    _send_update("111", "/watch AAPL")
    _send_update("111", "/unwatch AAPL")
    assert "AAPL" in fake_send.call_args.args[1]

    _send_update("111", "/watchlist")
    assert "目前沒有追蹤任何股票" in fake_send.call_args.args[1]


def test_unwatch_missing_argument_gives_usage(local_store, fake_send):
    _send_update("111", "/unwatch")

    body = fake_send.call_args.args[1]
    assert "用法" in body


def test_unwatch_nonexistent_symbol_is_non_error_reply(local_store, fake_send):
    _send_update("111", "/unwatch AAPL")

    body = fake_send.call_args.args[1]
    assert "不在" in body


def test_watchlists_are_isolated_across_chats(local_store, fake_send):
    _send_update("111", "/watch AAPL")
    _send_update("222", "/watch NVDA")

    _send_update("111", "/watchlist")
    assert "AAPL" in fake_send.call_args.args[1]
    assert "NVDA" not in fake_send.call_args.args[1]

    _send_update("222", "/watchlist")
    assert "NVDA" in fake_send.call_args.args[1]
    assert "AAPL" not in fake_send.call_args.args[1]


def test_subscribe_seeds_default_watchlist_for_new_chat(local_store, fake_send):
    _send_update("111", "/subscribe")
    _send_update("111", "/watchlist")

    body = fake_send.call_args.args[1]
    assert "AAPL" in body
    assert "NVDA" in body


def test_subscribe_does_not_overwrite_existing_custom_watchlist(local_store, fake_send):
    _send_update("111", "/watch TSLA")
    _send_update("111", "/subscribe")
    _send_update("111", "/watchlist")

    body = fake_send.call_args.args[1]
    assert "TSLA" in body
    assert "AAPL" not in body


def test_watch_and_unwatch_are_graceful_on_dynamodb_backend(monkeypatch, fake_send):
    monkeypatch.setenv("STORAGE_BACKEND", "dynamodb")

    _send_update("111", "/watch AAPL")
    body = fake_send.call_args.args[1]
    assert "僅本地模式" in body

    _send_update("111", "/unwatch AAPL")
    body = fake_send.call_args.args[1]
    assert "僅本地模式" in body
