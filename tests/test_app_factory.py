from httpx import ASGITransport, AsyncClient

from features import Features
from main import cors_settings, create_app, resolve_static_dir
from services.retrieval import SYSTEMS, enabled_systems


def _paths(app):
    return {r.path for r in app.routes}


def test_annotation_mode_has_no_search_or_benchmark_routes():
    app = create_app(Features(search=False, benchmark=False), static_dir="")
    paths = _paths(app)
    assert not any(p.startswith("/search") for p in paths)
    assert not any(p.startswith("/benchmark") for p in paths)
    assert any(p.startswith("/auth") for p in paths)


def test_router_groups_toggle_independently():
    app = create_app(Features(annotation=False, kv_pairs=False, benchmark=False), static_dir="")
    paths = _paths(app)
    assert "/search/bm25" in paths
    assert not any(p.startswith(("/auth", "/annotation", "/kv")) for p in paths)


def test_dense_and_cross_encoder_gate_endpoints():
    lexical = {
        s.slug for s in enabled_systems(Features(dense_retrieval=False, cross_encoder=False))
    }
    assert lexical == {"term-overlap", "tfidf", "bm25", "bm25-tf-idf", "bm25-prf"}
    no_jina = {s.slug for s in enabled_systems(Features(cross_encoder=False))}
    assert "semantic-rrf" in no_jina and "final-pipeline" not in no_jina
    assert {s.slug for s in enabled_systems(Features())} == set(SYSTEMS)
    assert enabled_systems(Features(search=False)) == []


def test_cors_settings():
    assert cors_settings("*") == {"allow_origins": ["*"], "allow_credentials": False}
    assert cors_settings("http://a, http://b ,") == {
        "allow_origins": ["http://a", "http://b"],
        "allow_credentials": True,
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


async def test_hadith_route(_patched_paths):
    app = create_app(Features(search=False, benchmark=False), static_dir="")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        found = (await c.get("/hadith/1")).json()
        assert found["Book"] == "Bukhari"
        assert (await c.get("/hadith/999")).json() == {"error": "not found"}


async def test_lifespan_reports_static_dir(_patched_paths, tmp_path, monkeypatch, capsys):
    (tmp_path / "index.html").write_text("x")
    monkeypatch.setattr("main.preload_resources", lambda f: None)
    app = create_app(Features(search=False), static_dir=str(tmp_path))
    async with app.router.lifespan_context(app):
        pass
    assert f"Serving frontend from: {tmp_path}" in capsys.readouterr().out
