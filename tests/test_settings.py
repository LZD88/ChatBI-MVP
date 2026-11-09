import pytest
from pydantic import ValidationError

from chatbi.settings import Settings


def test_runtime_validation_lists_missing_secrets() -> None:
    settings = Settings(_env_file=None)

    with pytest.raises(ValueError, match="OPENAI_API_KEY, INTENT_API_KEY, DB_USER, DB_PASSWORD"):
        settings.validate_query_runtime()


def test_settings_reject_invalid_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, query_max_rows=0)


def test_safe_snapshot_never_contains_secret_values() -> None:
    settings = Settings(
        _env_file=None,
        openai_api_key="sk-test-secret",
        intent_api_key="bailian-test-secret",
        db_user="chatbi_reader",
        db_password="database-secret",
    )

    rendered = repr(settings.safe_snapshot())

    assert "sk-test-secret" not in rendered
    assert "database-secret" not in rendered
    assert "bailian-test-secret" not in rendered
    assert settings.safe_snapshot()["openai_api_key_configured"] is True
    assert settings.safe_snapshot()["intent_api_key_configured"] is True
    assert settings.safe_snapshot()["db_password_configured"] is True
