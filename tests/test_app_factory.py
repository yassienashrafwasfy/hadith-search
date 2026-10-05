from httpx import ASGITransport, AsyncClient

from features import Features
from main import cors_settings, create_app, resolve_static_dir
from services.retrieval import SYSTEMS, enabled_systems


def _paths(app):
    return {r.path for r in app.routes}


def test_annotation_mode_has_no_search_or_benchmark_routes():
    app = create_app(Features(search=False, benchmark=False), static_dir="")
    paths = _paths(app)
    assert not any(p.startswith("/api/v1/search") for p in paths)
    assert not any(p.startswith("/api/v1/benchmark") for p in paths)
    assert "/api/v1/annotators" in paths and "/api/v1/tokens" in paths


def test_router_groups_toggle_independently():
    app = create_app(Features(annotation=False, kv_pairs=False, benchmark=False), static_dir="")
    paths = _paths(app)
    assert "/api/v1/searches" in paths
    prefixes = tuple(f"/api/v1/{name}" for name in ("annotators", "tokens", "assignments", "kv"))
    assert not any(p.startswith(prefixes) for p in paths)


def test_dense_flag_gates_endpoints():
    lexical = {s.slug for s in enabled_systems(Features(dense_retrieval=False))}
    assert lexical == {"term-overlap", "tfidf", "bm25", "bm25-tf-idf", "bm25-prf", "exact"}
    assert {s.slug for s in enabled_systems(Features())} == set(SYSTEMS)
    assert enabled_systems(Features(search=False)) == []


def test_cors_settings():
    assert cors_settings("*") == {"allow_origins": ["*"], "allow_credentials": False}
    assert cors_settings("http://a, http://b ,") == {
        "allow_origins": ["http://a", "http://b"],
        "allow_credentials": False,
    }


def test_resolve_static_dir_prefers_env(tmp_path):
    assert resolve_static_dir(str(tmp_path)) == str(tmp_path)


async def test_spa_fallback(tmp_path):
    (tmp_path / "index.html").write_text("<html>spa</html>")
    (tmp_path / "robots.txt").write_text("ok")
    app = create_app(Features(search=False, benchmark=False), static_dir=str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/robots.txt")).text == "ok"
        assert "spa" in (await c.get("/some/route")).text
        unknown_api = await c.get("/api/v1/nothing-here")
        assert unknown_api.status_code == 404
        assert unknown_api.headers["content-type"] == "application/problem+json"


async def test_lifespan_initialises_db_and_preloads(_patched_paths, monkeypatch, capsys):
    preloaded = []
    monkeypatch.setattr("main.preload_resources", lambda f: preloaded.append(f))
    features = Features(search=False, benchmark=False)
    app = create_app(features, static_dir="")
    async with app.router.lifespan_context(app):
        assert preloaded == [features]
        assert app.state.features is features
    out = capsys.readouterr().out
    assert "No frontend build found" in out and "Shutting down" in out


async def test_lifespan_disposes_engines_on_shutdown(_patched_paths, monkeypatch):
    calls = []

    async def fake_dispose():
        calls.append("disposed")

    monkeypatch.setattr("main.preload_resources", lambda f: None)
    monkeypatch.setattr("main.dispose_engines", fake_dispose)
    app = create_app(Features(search=False, benchmark=False), static_dir="")
    async with app.router.lifespan_context(app):
        assert calls == []
    assert calls == ["disposed"]


async def test_lifespan_disposes_engines_and_explains_when_db_is_unreachable(
    _patched_paths, monkeypatch
):
    import pytest

    calls = []

    async def fake_dispose():
        calls.append("disposed")

    async def broken_init():
        raise OSError("connection refused")

    monkeypatch.setattr("main.init_database", broken_init)
    monkeypatch.setattr("main.dispose_engines", fake_dispose)
    app = create_app(Features(search=False, benchmark=False), static_dir="")
    with pytest.raises(RuntimeError, match="Cannot reach the database"):
        async with app.router.lifespan_context(app):
            pass
    assert calls == ["disposed"]


async def test_lifespan_disposes_engines_when_the_app_errors(_patched_paths, monkeypatch):
    import pytest

    calls = []

    async def fake_dispose():
        calls.append("disposed")

    monkeypatch.setattr("main.preload_resources", lambda f: None)
    monkeypatch.setattr("main.dispose_engines", fake_dispose)
    app = create_app(Features(search=False, benchmark=False), static_dir="")
    with pytest.raises(ValueError):
        async with app.router.lifespan_context(app):
            raise ValueError("boom")
    assert calls == ["disposed"]


async def test_hadith_route(_patched_paths):
    app = create_app(Features(search=False, benchmark=False), static_dir="")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/api/v1/hadiths/1")).json()["Book"] == "Bukhari"
        assert (await c.get("/api/v1/hadiths/999")).status_code == 404


async def test_lifespan_reports_static_dir(_patched_paths, tmp_path, monkeypatch, capsys):
    (tmp_path / "index.html").write_text("x")
    monkeypatch.setattr("main.preload_resources", lambda f: None)
    app = create_app(Features(search=False), static_dir=str(tmp_path))
    async with app.router.lifespan_context(app):
        pass
    assert f"Serving frontend from: {tmp_path}" in capsys.readouterr().out


async def test_static_files_cannot_escape_the_static_dir(tmp_path):
    import httpx

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>spa</html>")
    (static / "app.js").write_text("ok")
    (tmp_path / "secret.txt").write_text("SECRET")
    app = create_app(
        Features(annotation=False, kv_pairs=False, benchmark=False, search=False),
        static_dir=str(static),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/app.js")).text == "ok"
        for path in ("/%2e%2e/secret.txt", "/..%2fsecret.txt", "/assets/../../secret.txt"):
            res = await c.get(path)
            assert "SECRET" not in res.text
            assert res.text == "<html>spa</html>"


async def test_security_headers_are_set_and_docs_keep_working(tmp_path):
    import httpx

    app = create_app(
        Features(annotation=False, kv_pairs=False, benchmark=False, search=False), static_dir=None
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        api = await c.get("/api/v1")
        docs = await c.get("/docs")
    assert api.headers["x-content-type-options"] == "nosniff"
    assert api.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in api.headers["content-security-policy"]
    assert (
        "content-security-policy" not in docs.headers and docs.headers["x-frame-options"] == "DENY"
    )
