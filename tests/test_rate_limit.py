"""Per-chat_id rate limiter: fixed-window counters + notify-once flag."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent.db.rate_limit import (
    DAY_LIMIT,
    DAY_LIMIT_MESSAGE,
    MINUTE_LIMIT,
    MINUTE_LIMIT_MESSAGE,
    check_rate_limit,
)

NOW = datetime(2026, 7, 26, 10, 0, 0, tzinfo=timezone.utc)


def test_minute_window_allows_up_to_limit(aws):
    for _ in range(MINUTE_LIMIT):
        allowed, message = check_rate_limit("111", now=NOW)
        assert allowed is True
        assert message is None


def test_minute_window_blocks_and_notifies_once_over_limit(aws):
    for _ in range(MINUTE_LIMIT):
        check_rate_limit("111", now=NOW)

    allowed, message = check_rate_limit("111", now=NOW)
    assert allowed is False
    assert message == MINUTE_LIMIT_MESSAGE

    # A second breach within the same minute window is silently dropped.
    allowed, message = check_rate_limit("111", now=NOW + timedelta(seconds=1))
    assert allowed is False
    assert message is None


def test_minute_window_recovers_in_next_minute(aws):
    for _ in range(MINUTE_LIMIT + 1):
        check_rate_limit("111", now=NOW)

    allowed, message = check_rate_limit("111", now=NOW + timedelta(minutes=1))
    assert allowed is True
    assert message is None


def test_day_window_blocks_and_notifies_once_over_limit(aws):
    # Stay under the per-minute limit by spacing calls a minute apart.
    for i in range(DAY_LIMIT):
        check_rate_limit("111", now=NOW + timedelta(minutes=i))

    allowed, message = check_rate_limit("111", now=NOW + timedelta(minutes=DAY_LIMIT))
    assert allowed is False
    assert message == DAY_LIMIT_MESSAGE

    allowed, message = check_rate_limit("111", now=NOW + timedelta(minutes=DAY_LIMIT + 1))
    assert allowed is False
    assert message is None


def test_day_window_recovers_next_day(aws):
    for i in range(DAY_LIMIT + 1):
        check_rate_limit("111", now=NOW + timedelta(minutes=i))

    allowed, message = check_rate_limit("111", now=NOW + timedelta(days=1))
    assert allowed is True
    assert message is None


def test_different_chat_ids_are_independent(aws):
    for _ in range(MINUTE_LIMIT):
        check_rate_limit("111", now=NOW)

    allowed, message = check_rate_limit("222", now=NOW)
    assert allowed is True
    assert message is None
