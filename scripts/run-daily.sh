#!/bin/bash
# launchd wrapper: loads .env then runs the daily earnings pipeline.
#
# launchd does not source the user's shell profile, so .env vars (Finnhub /
# Groq / Telegram keys) are never exported into the job's environment unless
# we load them explicitly here. See docs/local-daily-schedule.md.

set -e

cd /Users/yehhungwei/stock-agent

if [ ! -f .env ]; then
    echo ".env not found in /Users/yehhungwei/stock-agent — aborting" >&2
    exit 1
fi

set -a
source .env
set +a

make sync-earnings && make notify-earnings && make daily-summary && make sync-dynamodb
