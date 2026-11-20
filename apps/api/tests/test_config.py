import os

import pytest
from pydantic import SecretStr, ValidationError

# The module creates the process-wide application settings at import time. Give
# that instance safe test values; individual cases pass their own settings below.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "mysql+pymysql://nexus:test@localhost/nexus_test")
os.environ.setdefault("JWT_SECRET_KEY", "safe-test-signing-key-7f2a93c1d8b4e605")

from app.core.config import Settings  # noqa: E402
from app.core.security import create_access_token, decode_access_token  # noqa: E402
from uuid import uuid4  # noqa: E402


def make_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "database_url": "mysql+pymysql://nexus:test@localhost/nexus_test",
        "jwt_secret_key": "strong-test-key-7f2a93c1d8b4e605",
        "expose_temporary_passwords": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_jwt_secret_is_required(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError) as error:
        Settings(
            _env_file=None,
            app_env="production",
            database_url="mysql+pymysql://nexus:test@localhost/nexus_test",
        )
    assert "jwt_secret_key" in str(error.value)


@pytest.mark.parametrize("secret", ["short-key", "a" * 64])
def test_rejects_weak_jwt_secrets(secret):
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY") as error:
        make_settings(jwt_secret_key=secret)
    assert secret not in str(error.value)


@pytest.mark.parametrize("secret", [
    "replace_with_output_of_openssl_rand_hex_32",
    "LOCAL-ONLY-change-this-secret-before-deployment-0123456789",
    "your-secret-key-for-development-only",
])
def test_rejects_known_placeholders(secret):
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY") as error:
        make_settings(jwt_secret_key=secret)
    assert secret not in str(error.value)


def test_accepts_a_sufficient_random_secret_and_keeps_it_masked():
    secret = "8b34d1f0a7c29e6543b1d0f8a6c2e957"
    config = make_settings(app_env="production", jwt_secret_key=SecretStr(secret))
    assert config.jwt_signing_key == secret
    assert secret not in repr(config)


def test_temporary_password_response_is_disabled_by_default():
    assert make_settings().expose_temporary_passwords is False


def test_development_can_explicitly_expose_temporary_passwords():
    assert make_settings(app_env="development", expose_temporary_passwords=True).expose_temporary_passwords is True


def test_production_cannot_expose_temporary_passwords():
    with pytest.raises(ValidationError, match="only allowed in development"):
        make_settings(app_env="production", expose_temporary_passwords=True)


def test_test_environment_cannot_expose_temporary_passwords():
    with pytest.raises(ValidationError, match="only allowed in development"):
        make_settings(app_env="test", expose_temporary_passwords=True)


def test_access_token_signing_and_validation_share_configured_key():
    user_id, session_id = uuid4(), uuid4()
    token = create_access_token(user_id, session_id)
    assert decode_access_token(token) == (user_id, session_id)
