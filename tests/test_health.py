import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from features import Features
from rest import install_error_handlers
from routers import make_root_router

HEALTH = "/api/v1/health"


@pytest_asyncio.fixture
async def _health_client(_pg_schema):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(make_root_router(Features()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_health_is_ok_without_a_token_and_never_cached(_health_client):
    res = await _health_client.get(HEALTH)
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
    assert res.headers["cache-control"] == "no-store"


async def test_health_is_listed_on_the_api_root(_health_client):
    links = (await _health_client.get("/api/v1")).json()["_links"]
    assert links["health"] == {"href": HEALTH}


async def test_health_says_503_when_the_database_is_down(_health_client, monkeypatch):
    class _Down:
        async def __aenter__(self):
            raise ConnectionRefusedError("postgres://user:secret@10.0.0.9/db")

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr("routers.root.get_session", lambda: _Down())
    res = await _health_client.get(HEALTH)
    assert res.status_code == 503
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.headers["cache-control"] == "no-store"
    assert "secret" not in res.text and "10.0.0.9" not in res.text


async def test_health_gives_up_when_the_database_hangs(_health_client, monkeypatch):
    import asyncio

    class _Slow:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def execute(self, *_args):
            await asyncio.sleep(5)

    monkeypatch.setattr("routers.root.get_session", lambda: _Slow())
    monkeypatch.setattr("routers.root.HEALTH_TIMEOUT_SECONDS", 0.05)
    assert (await _health_client.get(HEALTH)).status_code == 503


@pytest.mark.parametrize("mode", [Features(search=False, annotation=False), Features()])
async def test_health_exists_in_every_mode(_pg_schema, mode):
    app = FastAPI()
    app.include_router(make_root_router(mode))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get(HEALTH)).status_code == 200
