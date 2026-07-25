"""Verify agent.prompts exports the expected non-empty string constants."""

from __future__ import annotations

from agent import prompts


def test_prompt_constants_are_nonempty_strings():
    for name in ("SYSTEM_INSTRUCTION", "SUMMARY_SYSTEM_PROMPT", "COMPANY_PROMPT_TEMPLATE"):
        value = getattr(prompts, name)
        assert isinstance(value, str)
        assert value.strip()
