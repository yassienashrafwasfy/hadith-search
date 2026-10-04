"""Error hygiene: failures say little, and prod hides the API docs."""

import pytest

import routers.hadiths as hadiths_module
from security._helpers import API, assert_clean

_PROD = {
    "app_env": "prod",
    "auth_secret": "p" * 40,
    "cors_origins": "https://hadith.example",
}
_DOC_PATHS = ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect")


async def test_client_errors_are_problem_json_with_the_documented_fields(_api, _alice):
    cases = [
        await _api.get(f"{API}/nothing-here"),  # 404
        await _api.get(f"{API}/hadiths/99999"),  # 404 from the handler
        await _api.delete(f"{API}/tokens"),  # 405
        await _api.get(f"{API}/assignments"),  # 401
        await _api.get(f"{API}/hadiths/abc"),  # 422
        await _api.get(f"{API}/assignments/q2/../x", headers=_alice["headers"]),
    ]
    for res in cases:
        assert 400 <= res.status_code < 500
        assert_clean(res)
        body = res.json()
        assert {"type", "title", "status", "detail"} <= set(body)
        assert body["status"] == res.status_code


async def test_a_server_crash_shows_no_detail(_api, monkeypatch):
    """Whatever the exception says (a connection string, a path, SQL) stays in the server log."""

    def boom(_id):
        raise RuntimeError("postgresql://hadith:S3CRET-PW@db:5432/hadith at /app/backend/x.py")

    monkeypatch.setattr(hadiths_module, "get_hadith_row", boom)
    res = await _api.get(f"{API}/hadiths/1")
    assert res.status_code == 500
    for secret in ("S3CRET-PW", "postgresql", "/app/backend", "RuntimeError", "Traceback"):
        assert secret not in res.text
    assert len(res.text) < 100


async def test_real_database_errors_show_no_sql(_api, _alice):
    """The id is past the 32-bit column, so PostgreSQL itself raises; the client sees nothing of it."""
    res = await _api.get(f"{API}/hadiths/{2**40}")
    assert res.status_code in (404, 422, 500)
    assert "psycopg" not in res.text and "SELECT" not in res.text and "DataError" not in res.text


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING (low): an unhandled exception is answered by Starlette's own 500 page, plain "
        "text 'Internal Server Error', not application/problem+json like every other error "
        "(R-07), and it carries none of the security headers. Suggested fix: add an Exception "
        "handler in backend/rest.py install_error_handlers that returns problem_response(500, "
        "'Internal server error')."
    ),
)
async def test_a_500_is_problem_json_too(_api, monkeypatch):
    monkeypatch.setattr(hadiths_module, "get_hadith_row", lambda _id: 1 / 0)
    res = await _api.get(f"{API}/hadiths/1")
    assert res.status_code == 500
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.headers["x-content-type-options"] == "nosniff"


# ---------- prod hides the docs ----------


@pytest.mark.parametrize("path", _DOC_PATHS[:3])
async def test_dev_serves_the_docs(_make_app, _open, path):
    async with _open(_make_app(app_env="dev")) as api:
        res = await api.get(path)
    assert res.status_code == 200 and (
        "openapi" in res.text.lower() or "swagger" in res.text.lower()
    )


@pytest.mark.parametrize("path", _DOC_PATHS)
async def test_prod_has_no_docs_and_no_schema(_make_app, _open, path):
    async with _open(_make_app(**_PROD)) as api:
        res = await api.get(path)
    # With the frontend mounted the catch-all answers with the page shell; it must not be a doc.
    text = res.text.lower()
    assert "swagger" not in text and "redoc" not in text and '"openapi"' not in text
    assert '"paths"' not in text and "/api/v1/tokens" not in text
    assert not res.headers["content-type"].startswith("application/json")


async def test_prod_api_errors_are_just_as_quiet(_make_app, _open):
    async with _open(_make_app(**_PROD)) as api:
        for res in (
            await api.get(f"{API}/hadiths/abc"),
            await api.get(f"{API}/assignments"),
            await api.post(f"{API}/tokens", json={}),
        ):
            assert_clean(res)
            assert "input" not in res.text and "ctx" not in res.text


async def test_health_is_public_and_leaks_nothing_when_the_database_is_down(_api, monkeypatch):
    import routers.root as root_module

    ok = await _api.get(f"{API}/health")
    assert ok.status_code == 200 and ok.json() == {"status": "ok"}

    def broken():
        raise RuntimeError("postgresql://hadith:S3CRET-PW@db:5432/hadith")

    monkeypatch.setattr(root_module, "get_session", broken)
    down = await _api.get(f"{API}/health")
    assert_clean(down, allow=[503])
    assert "S3CRET-PW" not in down.text and "RuntimeError" not in down.text
    assert down.headers["cache-control"] == "no-store"
