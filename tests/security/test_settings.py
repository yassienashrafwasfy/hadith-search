"""Settings: prod refuses weak or missing configuration, and secrets stay out of text."""

import logging
from pathlib import Path

import pytest
import yaml
from conftest import TEST_SECRET
from pydantic import ValidationError

from settings import MIN_SECRET_LENGTH, Settings, get_settings
from tokens import AuthSettings, auth_settings

ROOT = Path(__file__).resolve().parents[2]
GOOD = {"app_env": "prod", "auth_secret": "p" * 40, "cors_origins": "https://hadith.example"}


def test_a_complete_prod_configuration_is_accepted():
    assert Settings(**GOOD).is_prod


@pytest.mark.parametrize("missing", ["auth_secret", "cors_origins"])
@pytest.mark.parametrize("empty", [None, ""])
def test_prod_refuses_a_missing_or_empty_setting(missing, empty):
    with pytest.raises(ValidationError, match=missing.upper()):
        Settings(**{**GOOD, missing: empty})


@pytest.mark.parametrize("env", ["prod", "dev", "test"])
@pytest.mark.parametrize("length", [1, 8, MIN_SECRET_LENGTH - 1])
def test_a_short_secret_is_refused_in_every_mode(env, length):
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(**{**GOOD, "app_env": env, "auth_secret": "s" * length})


def test_the_minimum_length_secret_is_accepted():
    assert Settings(**{**GOOD, "auth_secret": "s" * MIN_SECRET_LENGTH})


@pytest.mark.parametrize("name", ["production", "PROD ", "prod2", "live", "staging", ""])
def test_a_misspelt_app_env_stops_the_start_instead_of_falling_back_to_dev(name):
    with pytest.raises(ValidationError):
        Settings(app_env=name)


def test_the_environment_variables_drive_the_same_rules(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    get_settings.cache_clear()
    with pytest.raises(ValidationError):
        get_settings()


# ---------- secrets stay out of text ----------


def test_the_secret_and_the_database_password_are_not_in_repr_or_str():
    s = Settings(**GOOD, database_url="postgresql+psycopg://u:DB-PASSWORD-1@h/d")
    for text in (repr(s), str(s), repr(s.auth_secret), str(s.auth_secret)):
        assert "p" * 40 not in text and "DB-PASSWORD-1" not in text


def test_the_auth_secret_is_masked_in_a_json_dump():
    assert "p" * 40 not in Settings(**GOOD).model_dump_json()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING (low): database_url hides itself in repr but model_dump() / model_dump_json() "
        "print the full URL with the password. Nothing dumps Settings today. Suggested fix: make "
        "database_url a SecretStr in backend/settings.py (and read it with get_secret_value() "
        "in backend/database.py), or add exclude=True."
    ),
)
def test_the_database_password_is_masked_in_a_json_dump():
    s = Settings(**GOOD, database_url="postgresql+psycopg://u:DB-PASSWORD-1@h/d")
    assert "DB-PASSWORD-1" not in s.model_dump_json()


def test_the_signing_secret_is_not_in_the_repr_of_auth_settings():
    assert TEST_SECRET not in repr(AuthSettings(TEST_SECRET, 60))


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING (low): when AUTH_SECRET is too short, pydantic's error text quotes the input, "
        "so the rejected secret (and the start of DATABASE_URL, password included when the URL "
        "is short) is printed in the start-up log. Suggested fix: raise the error from a "
        "field_validator on auth_secret instead of the model validator, or catch "
        "ValidationError in startup and log only the field names."
    ),
)
def test_a_rejected_secret_is_not_printed_in_the_error():
    with pytest.raises(ValidationError) as caught:
        Settings(app_env="dev", auth_secret="almost-secret-7", database_url="postgresql://u:pw@h/d")
    assert "almost-secret-7" not in str(caught.value)


def test_a_generated_secret_is_not_logged(caplog, monkeypatch):
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    auth_settings.cache_clear()
    get_settings.cache_clear()
    with caplog.at_level(logging.DEBUG):
        generated = auth_settings().secret
    auth_settings.cache_clear()
    assert len(generated) >= 32 and "AUTH_SECRET is not set" in caplog.text
    assert generated not in caplog.text


def test_two_servers_without_a_configured_secret_do_not_share_one(monkeypatch):
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    secrets_seen = set()
    for _ in range(2):
        auth_settings.cache_clear()
        get_settings.cache_clear()
        secrets_seen.add(auth_settings().secret)
    auth_settings.cache_clear()
    assert len(secrets_seen) == 2  # random per process, never a fixed default


def test_the_env_example_ships_no_real_secret():
    for line in (ROOT / ".env.example").read_text().splitlines():
        name, _, value = line.partition("=")
        if name.strip() in {"AUTH_SECRET", "POSTGRES_PASSWORD", "LLM_API_KEY"}:
            assert value.strip() == "", line


def test_the_compose_file_starts_the_app_in_prod_mode():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    env = next(
        s for s in compose["services"].values() if "AUTH_SECRET" in str(s.get("environment"))
    )["environment"]
    assert "APP_ENV=prod" in env
    assert "CORS_ORIGINS=*" not in env
    # no insecure default: compose stops with a message when it is not set
    assert any(item.startswith("CORS_ORIGINS=${CORS_ORIGINS:?") for item in env)
    assert any(item.startswith("AUTH_SECRET=${AUTH_SECRET:?") for item in env)
