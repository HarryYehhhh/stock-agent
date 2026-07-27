"""Chat CLI REPL tests: multi-turn history passing and exit behaviors."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from agent import chat_cli


@pytest.fixture
def fake_run_agent(monkeypatch):
    mock = MagicMock(side_effect=lambda msg, history: f"echo:{msg}")
    monkeypatch.setattr(chat_cli, "run_agent", mock)
    return mock


def _feed_input(monkeypatch, lines):
    it = iter(lines)

    def _fake_input(prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", _fake_input)


def test_main_configures_info_level_logging(monkeypatch, fake_run_agent):
    """agent.py 的 INFO log（tool 呼叫、playbook 注入、groq usage）本地 CLI 除錯要看得到。"""
    mock_basic_config = MagicMock()
    monkeypatch.setattr(chat_cli.logging, "basicConfig", mock_basic_config)
    _feed_input(monkeypatch, ["exit"])

    chat_cli.main()

    mock_basic_config.assert_called_once()
    assert mock_basic_config.call_args.kwargs["level"] == logging.INFO


def test_main_silences_httpx_info_logs_to_avoid_leaking_finnhub_token(monkeypatch, fake_run_agent):
    """httpx 的 request log 在 INFO 級會印出完整 URL（含 Finnhub token 查詢參數）。"""
    _feed_input(monkeypatch, ["exit"])

    chat_cli.main()

    assert logging.getLogger("httpx").level == logging.WARNING


def test_exit_command_stops_loop_without_calling_agent(monkeypatch, fake_run_agent):
    _feed_input(monkeypatch, ["exit"])
    chat_cli.main()
    fake_run_agent.assert_not_called()


def test_quit_command_stops_loop_without_calling_agent(monkeypatch, fake_run_agent):
    _feed_input(monkeypatch, ["quit"])
    chat_cli.main()
    fake_run_agent.assert_not_called()


def test_eof_stops_loop_gracefully(monkeypatch, fake_run_agent):
    _feed_input(monkeypatch, [])
    chat_cli.main()
    fake_run_agent.assert_not_called()


def test_blank_line_is_skipped_without_calling_agent(monkeypatch, fake_run_agent):
    _feed_input(monkeypatch, ["", "exit"])
    chat_cli.main()
    fake_run_agent.assert_not_called()


def test_single_turn_calls_run_agent_with_empty_history(monkeypatch, fake_run_agent, capsys):
    _feed_input(monkeypatch, ["hello", "exit"])
    chat_cli.main()

    fake_run_agent.assert_called_once_with("hello", [])
    out = capsys.readouterr().out
    assert "echo:hello" in out


def test_second_turn_passes_first_turn_as_history(monkeypatch, fake_run_agent):
    _feed_input(monkeypatch, ["first", "second", "exit"])
    chat_cli.main()

    assert fake_run_agent.call_args_list[0].args == ("first", [])
    assert fake_run_agent.call_args_list[1].args == (
        "second",
        [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "echo:first"},
        ],
    )


def test_history_capped_at_limit(monkeypatch, fake_run_agent):
    lines = [f"msg{i}" for i in range(chat_cli.HISTORY_LIMIT + 5)] + ["exit"]
    _feed_input(monkeypatch, lines)
    chat_cli.main()

    last_call_history = fake_run_agent.call_args_list[-1].args[1]
    assert len(last_call_history) == chat_cli.HISTORY_LIMIT
