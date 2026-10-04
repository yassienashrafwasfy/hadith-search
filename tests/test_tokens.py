import logging

import jwt
import pytest
from pydantic import ValidationError

from tokens import DEFAULT_TTL_MINUTES, AuthSettings, auth_settings, issue_token, read_token

SETTINGS = AuthSettings(secret="s" * 40, ttl_seconds=60)


@pytest.fixture(autouse=True)
def _fresh_settings():
    auth_settings.cache_clear()
    yield
    auth_settings.cache_clear()


def test_token_roundtrip_carries_id_and_name():
    token = issue_token(7, "alice", SETTINGS)
    assert read_token(token, SETTINGS) == {"id": 7, "username": "alice"}
    claims = jwt.decode(token, SETTINGS.secret, algorithms=["HS256"])
    assert claims["sub"] == "7" and claims["exp"] - claims["iat"] == 60


@pytest.mark.parametrize("bad", ["", "abc", "a.b.c"])
def test_garbage_tokens_read_as_none(bad):
    assert read_token(bad, SETTINGS) is None


def test_other_secret_and_missing_claims_are_rejected():
    other = AuthSettings(secret="o" * 40, ttl_seconds=60)
    assert read_token(issue_token(1, "a", other), SETTINGS) is None
    no_exp = jwt.encode({"sub": "1", "name": "a"}, SETTINGS.secret, algorithm="HS256")
    assert read_token(no_exp, SETTINGS) is None
    no_name = jwt.encode({"sub": "1", "exp": 9999999999}, SETTINGS.secret, algorithm="HS256")
    assert read_token(no_name, SETTINGS) is None
    text_id = jwt.encode(
        {"sub": "x", "name": "a", "exp": 9999999999}, SETTINGS.secret, algorithm="HS256"
    )
    assert read_token(text_id, SETTINGS) is None


def test_none_algorithm_is_not_accepted():
    unsigned = jwt.encode({"sub": "1", "name": "a", "exp": 9999999999}, None, algorithm="none")
    assert read_token(unsigned, SETTINGS) is None


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "k" * 32)
    monkeypatch.setenv("AUTH_TOKEN_TTL_MINUTES", "5")
    assert auth_settings() == AuthSettings(secret="k" * 32, ttl_seconds=300)


def test_default_ttl_and_random_secret_warns(monkeypatch, caplog):
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    monkeypatch.delenv("AUTH_TOKEN_TTL_MINUTES", raising=False)
    with caplog.at_level(logging.WARNING):
        settings = auth_settings()
    assert settings.ttl_seconds == DEFAULT_TTL_MINUTES * 60 and len(settings.secret) >= 32
    assert "AUTH_SECRET is not set" in caplog.text


def test_short_secret_is_refused(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", "short")
    with pytest.raises(ValidationError, match="at least 32"):
        auth_settings()
