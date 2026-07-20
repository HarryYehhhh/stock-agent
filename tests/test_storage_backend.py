import pytest

from tests.conftest import clear_db_caches


def test_defaults_to_local_outside_lambda(monkeypatch):
    from agent.db.backend import selected_backend_name

    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    clear_db_caches()

    assert selected_backend_name() == "local"


def test_defaults_to_dynamodb_in_lambda(monkeypatch):
    from agent.db.backend import selected_backend_name

    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "fn")
    clear_db_caches()

    assert selected_backend_name() == "dynamodb"


def test_storage_backend_override(monkeypatch):
    from agent.db.backend import selected_backend_name

    monkeypatch.setenv("STORAGE_BACKEND", "dynamodb")
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    clear_db_caches()

    assert selected_backend_name() == "dynamodb"


def test_invalid_storage_backend_fails(monkeypatch):
    from agent.db.backend import selected_backend_name

    monkeypatch.setenv("STORAGE_BACKEND", "bogus")
    clear_db_caches()

    with pytest.raises(ValueError, match="Unsupported STORAGE_BACKEND"):
        selected_backend_name()
