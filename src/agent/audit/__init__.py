"""Offline data-snapshot audit + LLM-judge pipeline (ADR-0010).

Purely local batch tooling: dumps DynamoDB tables into dated markdown
snapshots (`dump`) and runs a BM25-retrieval + Groq LLM-judge pass over
existing conversations to check whether bot answers are grounded in the
snapshot (`judge`). Does not touch the online bot runtime.

Usage: `python -m agent.audit <dump|judge> [options]`
"""
