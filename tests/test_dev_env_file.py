"""Reproduce and verify the fix for `make dev` not loading `.env`.

Root cause: `make dev` used to start uvicorn with no dotenv mechanism at all, so on a
clean shell (no vars exported), `config._load_secrets()` hit `os.environ["FINNHUB_API_KEY"]`
and raised `KeyError`, which any endpoint touching secrets (e.g. GET /market-summary)
let bubble up as an unhandled 500.

Fix: `make dev` now passes `uvicorn ... --env-file .env`. uvicorn's `--env-file` flag
maps directly to `uvicorn.Config(env_file=...)`, whose `__init__` calls
`dotenv.load_dotenv(dotenv_path=env_file)` immediately (see uvicorn.Config.__init__).
So instantiating `uvicorn.Config(env_file=...)` here reproduces exactly what the CLI
flag does at process startup, without needing to spawn a real server.
"""

from __future__ import annotations

import pytest
import uvicorn

from agent import config

SECRET_ENV_VARS = [
    "FINNHUB_API_KEY",
    "GROQ_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
]


@pytest.fixture
def clean_secrets(monkeypatch):
    """Simulate a clean shell where no secret env vars are exported."""
    for name in SECRET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    config._load_secrets.cache_clear()
    yield
    config._load_secrets.cache_clear()


def test_load_secrets_raises_keyerror_without_env_file(clean_secrets):
    """Reproduces the bug: no `.env` loaded -> KeyError from os.environ[...]."""
    with pytest.raises(KeyError):
        config.finnhub_api_key()


def test_uvicorn_env_file_flag_loads_dotenv_before_secrets_are_read(
    tmp_path, clean_secrets
):
    """Verifies the fix: `--env-file .env` populates os.environ so secrets load."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FINNHUB_API_KEY=test-finnhub-key\n"
        "GROQ_API_KEY=test-groq-key\n"
        "TELEGRAM_BOT_TOKEN=test-bot-token\n"
        "TELEGRAM_WEBHOOK_SECRET=test-webhook-secret\n"
    )

    uvicorn.Config(app="agent.local_server:app", env_file=str(env_file))

    assert config.finnhub_api_key() == "test-finnhub-key"
    assert config.groq_api_key() == "test-groq-key"
    assert config.telegram_bot_token() == "test-bot-token"
    assert config.telegram_webhook_secret() == "test-webhook-secret"
