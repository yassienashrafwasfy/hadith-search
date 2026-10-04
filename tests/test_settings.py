import pytest
from pydantic import ValidationError

from main import create_app
from settings import DEFAULT_CORS_ORIGINS, Environment, Settings, get_settings

SECRET = "s" * 32


def _prod_env(monkeypatch, **overrides):
    values = {"APP_ENV": "prod", "AUTH_SECRET": SECRET, "CORS_ORIGINS": "https://hadith.example"}
    values.update(overrides)
    for name, value in values.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def test_defaults_are_dev(monkeypatch):
    for name in ("APP_ENV", "AUTH_SECRET", "CORS_ORIGINS", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.app_env is Environment.DEV and not settings.is_prod
    assert settings.effective_cors_origins == DEFAULT_CORS_ORIGINS
    assert settings.auth_secret is None and settings.database_url is None


def test_reads_the_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h/d")
    monkeypatch.setenv("AUTH_TOKEN_TTL_MINUTES", "5")
    monkeypatch.setenv("ARABIC_ENCODER_THREADS", "3")
    settings = Settings()
    assert settings.database_url == "postgresql+psycopg://u:p@h/d"
    assert settings.auth_token_ttl_minutes == 5 and settings.arabic_encoder_threads == 3


def test_secret_and_url_do_not_show_in_repr(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:hunter2@h/d")
    text = repr(Settings())
    assert SECRET not in text and "hunter2" not in text


def test_unknown_environment_is_refused(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(ValidationError):
        Settings()


def test_get_settings_is_built_once(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    assert get_settings() is get_settings()
    first = get_settings()
    get_settings.cache_clear()
    assert get_settings() is not first


@pytest.mark.parametrize("missing", ["AUTH_SECRET", "CORS_ORIGINS"])
def test_prod_needs_a_secret_and_explicit_cors(monkeypatch, missing):
    _prod_env(monkeypatch, **{missing: None})
    with pytest.raises(ValidationError, match=missing):
        Settings()


def test_prod_with_everything_set_is_valid(monkeypatch):
    _prod_env(monkeypatch)
    settings = Settings()
    assert settings.is_prod and settings.effective_cors_origins == "https://hadith.example"


def test_prod_still_checks_secret_length(monkeypatch):
    _prod_env(monkeypatch, AUTH_SECRET="short")
    with pytest.raises(ValidationError, match="at least 32"):
        Settings()


def test_prod_hides_docs_and_schema(monkeypatch):
    _prod_env(monkeypatch)
    app = create_app(static_dir="", settings=Settings())
    assert app.docs_url is None and app.redoc_url is None and app.openapi_url is None


def test_dev_keeps_docs_and_schema(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    app = create_app(static_dir="", settings=Settings())
    assert app.docs_url == "/docs" and app.openapi_url == "/openapi.json"


def test_test_environment_behaves_like_dev(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    settings = Settings()
    assert settings.app_env is Environment.TEST and not settings.is_prod
