"""Corrections review CLI (ADR-0014).

Reads/writes the `corrections` table (SQLite locally, DynamoDB in Lambda's
env) populated by the `/correct` Telegram command
(agent.processor_handler._process_update). Usage:
`python -m agent.corrections <list|show|approve|reject|export> [options]`.
"""
