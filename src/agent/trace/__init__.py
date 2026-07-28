"""Request trace query CLI (ADR-0013).

Reads the `traces` table (SQLite locally, DynamoDB in Lambda's env) written
by `agent.agent.run_agent()` on every call. Usage:
`python -m agent.trace list [options]`.
"""
