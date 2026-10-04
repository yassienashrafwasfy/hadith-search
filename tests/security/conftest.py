"""Fixtures for the security suite: the real `create_app` over the per-test schema.

Unlike `_client` in tests/conftest.py (a hand-built app with some routers), these tests use the
app the way production builds it: `main.create_app` with its CORS, security-header and error
middleware, the search limiter and every router. Only two things are swapped, as in the other
tests: the token secret (a known one, so tests can forge tokens) and the search context (a fake
encoder instead of the ONNX model). The `_search_index` fixture supplies the 3-hadith corpus.
"""

from collections.abc import Iterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from conftest import TEST_SECRET
from httpx import ASGITransport, AsyncClient

import database
from features import Features
from main import create_app
from routers.search import get_search_context
from security._helpers import _sign_up
from services.retrieval import SearchContext
from settings import Settings
from tokens import AuthSettings, auth_settings

ALL_FEATURES = Features(annotation=True, kv_pairs=True, benchmark=True, search=True)


@pytest.fixture
def _static_dir(tmp_path):
    """A tiny frontend build, so the app is created with its catch-all route like in production."""
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>spa</html>")
    return static


@pytest.fixture
def _make_app(_search_index, _fake_model, _mock_preprocess, _static_dir):
    """Build the real app. Settings come from keyword arguments (APP_ENV and so on)."""

    def build(**settings) -> "FastAPI":  # noqa: F821
        settings.setdefault("auth_secret", TEST_SECRET)
        app = create_app(ALL_FEATURES, static_dir=str(_static_dir), settings=Settings(**settings))

        def context() -> Iterator[SearchContext]:
            with database.get_sync_session() as session:
                yield SearchContext(session=session, model=lambda: _fake_model)

        app.dependency_overrides[get_search_context] = context
        app.dependency_overrides[auth_settings] = lambda: AuthSettings(TEST_SECRET, 3600)
        return app

    return build


@pytest.fixture
def _open():
    """Async context manager giving an HTTP client for an app.

    Unhandled server errors come back as the real 500 response (what a user would see) instead
    of being re-raised into the test.
    """

    @asynccontextmanager
    async def opened(app, **client_kwargs):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test", **client_kwargs) as c:
            yield c

    return opened


@pytest_asyncio.fixture
async def _app(_make_app):
    return _make_app(app_env="test")


@pytest_asyncio.fixture
async def _api(_app, _open):
    async with _open(_app) as client:
        yield client


@pytest_asyncio.fixture
async def _alice(_api):
    """Gets q1 and q2 (the first two sign-ups are assigned in query order)."""
    return await _sign_up(_api, "alice")


@pytest_asyncio.fixture
async def _bob(_api, _alice):
    """Signs up after alice: gets q3 and q1, so he shares q1 with her and never sees q2."""
    return await _sign_up(_api, "bob")


@pytest_asyncio.fixture
async def _kv_pair(_patched_paths):
    from models import KvPair

    async with database.get_session() as session:
        session.add(
            KvPair(
                topic="prayer",
                language="en",
                concept_en="c",
                concept_ar="ك",
                entity_en="e",
                entity_ar="ع",
                hadith_id=1,
                status="pending",
                created_at=database.now_iso(),
            )
        )
        await session.commit()
