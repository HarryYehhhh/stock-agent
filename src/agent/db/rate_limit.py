"""Per-chat_id rate limiting: 3 messages/minute, 15 messages/day.

Fixed-window counters keyed by (chat_id, window) via
StorageBackend.increment_rate_limit (atomic ADD). When a window is
breached, StorageBackend.mark_rate_limit_notified atomically flips a
"notified" flag so the "please slow down" reply is sent at most once per
window; further messages in that window are silently dropped.

webhook_handler calls check_rate_limit() before enqueuing to SQS.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent.db.backend import get_backend

MINUTE_LIMIT = 3
DAY_LIMIT = 15
MINUTE_WINDOW_TTL_SECONDS = 120  # covers the 60s window plus clock skew
DAY_WINDOW_TTL_SECONDS = 26 * 3600  # covers a full UTC day plus buffer

MINUTE_LIMIT_MESSAGE = "您傳送訊息的速度有點快，請稍等一下再繼續 🙏"
DAY_LIMIT_MESSAGE = "您今天的訊息額度已經用完囉，請明天再來 🙏"


def check_rate_limit(chat_id: str, now: datetime | None = None) -> tuple[bool, str | None]:
    """Check and record one message from chat_id against the minute/day limits.

    Returns (allowed, notify_message):
    - (True, None): under both limits, caller should proceed.
    - (False, message): first message to breach a limit this window —
      caller should send `message` once, then drop the message.
    - (False, None): limit already breached and already notified this
      window — caller should silently drop the message.
    """
    now = now or datetime.now(timezone.utc)
    ts = int(now.timestamp())
    backend = get_backend()

    minute_window = f"minute#{ts // 60}"
    minute_count = backend.increment_rate_limit(
        chat_id, minute_window, ts + MINUTE_WINDOW_TTL_SECONDS
    )
    if minute_count > MINUTE_LIMIT:
        if backend.mark_rate_limit_notified(chat_id, minute_window):
            return False, MINUTE_LIMIT_MESSAGE
        return False, None

    day_window = f"day#{now.strftime('%Y-%m-%d')}"
    day_count = backend.increment_rate_limit(chat_id, day_window, ts + DAY_WINDOW_TTL_SECONDS)
    if day_count > DAY_LIMIT:
        if backend.mark_rate_limit_notified(chat_id, day_window):
            return False, DAY_LIMIT_MESSAGE
        return False, None

    return True, None
