import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pytest_bdd import given, parsers, scenarios, then, when

from features import Features
from rest import install_error_handlers
from routers import make_root_router

scenarios("../../docs/behaviours/health.feature")


@pytest_asyncio.fixture
async def _client(_pg_schema):
    """Replaces the shared client: only the root router, which is where /health lives."""
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(make_root_router(Features()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@given("the database is reachable")
def _db_up():
    pass


@given("the database is not reachable")
def _db_down(monkeypatch):
    class _Down:
        async def __aenter__(self):
            raise ConnectionRefusedError("postgres://user:secret@10.0.0.9/db")

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr("routers.root.get_session", lambda: _Down())


@when(parsers.re(r"a client calls GET (?P<path>\S+) without a token"))
def _get(_api, path):
    _api("GET", path)


@then(parsers.parse('the body status is "{value}"'))
def _body_status(_ctx, value):
    assert _ctx["responses"][0].json()["status"] == value


@then("the answer is never cached")
def _never_cached(_ctx):
    assert _ctx["responses"][0].headers["cache-control"] == "no-store"


@then("the body does not mention the database address")
def _no_address(_ctx):
    assert "10.0.0.9" not in _ctx["responses"][0].text


@then("the root lists a health link")
def _root_has_health(_ctx):
    assert _ctx["responses"][0].json()["_links"]["health"]["href"] == "/api/v1/health"
