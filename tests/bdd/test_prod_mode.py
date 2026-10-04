import pytest
from pydantic import ValidationError
from pytest_bdd import given, parsers, scenarios, then, when

from main import create_app
from settings import Settings, get_settings

scenarios("../../docs/behaviours/prod-mode.feature")

SECRET = "s" * 32


def _env(monkeypatch, **values):
    for name, value in values.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


def _prod(monkeypatch, **overrides):
    values = {"APP_ENV": "prod", "AUTH_SECRET": SECRET, "CORS_ORIGINS": "https://hadith.example"}
    _env(monkeypatch, **{**values, **overrides})


@given('APP_ENV is "prod" with a valid AUTH_SECRET and CORS_ORIGINS')
def _valid_prod(monkeypatch):
    _prod(monkeypatch)


@given("APP_ENV is not set")
def _no_app_env(monkeypatch):
    _env(monkeypatch, APP_ENV=None)


@given(parsers.parse('APP_ENV is "prod" and {missing} is not set'))
def _prod_without(monkeypatch, missing):
    _prod(monkeypatch, **{missing: None})


@given(parsers.parse('APP_ENV is "prod" and CORS_ORIGINS is "{origins}"'))
def _prod_with_origins(monkeypatch, origins):
    _prod(monkeypatch, CORS_ORIGINS=origins)


@given(parsers.parse('APP_ENV is "dev" and CORS_ORIGINS is "{origins}"'))
def _dev_with_origins(monkeypatch, origins):
    _env(monkeypatch, APP_ENV="dev", CORS_ORIGINS=origins)


@given('APP_ENV is "prod" and AUTH_SECRET is "short"')
def _prod_short_secret(monkeypatch):
    _prod(monkeypatch, AUTH_SECRET="short")


@given('APP_ENV is "test" and AUTH_SECRET is not set')
def _test_env(monkeypatch):
    _env(monkeypatch, APP_ENV="test", AUTH_SECRET=None)


@given("APP_ENV is an unknown value")
def _unknown_env(monkeypatch):
    _env(monkeypatch, APP_ENV="staging")


@when("the app is created")
def _create_app(_ctx):
    _ctx["app"] = create_app(static_dir="", settings=Settings())


@when("Settings are built")
def _build_settings(_ctx):
    try:
        Settings()
    except ValidationError as error:
        _ctx["error"] = error


@then("docs_url, redoc_url and openapi_url are all None")
def _docs_hidden(_ctx):
    app = _ctx["app"]
    assert (app.docs_url, app.redoc_url, app.openapi_url) == (None, None, None)


@then('docs_url is "/docs" and openapi_url is "/openapi.json"')
def _docs_kept(_ctx):
    app = _ctx["app"]
    assert (app.docs_url, app.openapi_url) == ("/docs", "/openapi.json")


@then("a validation error is raised")
def _validation_error(_ctx):
    assert isinstance(_ctx.get("error"), ValidationError)


@then("the error says the secret needs at least 32 characters")
def _secret_length(_ctx):
    assert "at least 32" in str(_ctx["error"])


@then("the error says CORS_ORIGINS must not be *")
def _wildcard_refused(_ctx):
    assert "CORS_ORIGINS=*" in str(_ctx["error"])


@then("no error is raised")
def _no_error(_ctx):
    assert "error" not in _ctx


@then("it starts, like in dev")
def _starts_like_dev(_ctx):
    assert _ctx["app"].docs_url == "/docs"


@then("Settings refuses it")
def _refuses():
    with pytest.raises(ValidationError):
        Settings()


@when("get_settings is called twice")
def _call_twice(_ctx):
    _ctx["first"], _ctx["second"] = get_settings(), get_settings()


@then("both calls return the same object until get_settings.cache_clear() runs")
def _same_object(_ctx):
    assert _ctx["first"] is _ctx["second"]
    get_settings.cache_clear()
    assert get_settings() is not _ctx["first"]
