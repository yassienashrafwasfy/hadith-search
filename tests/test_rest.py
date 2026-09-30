"""Cross-cutting REST behaviour: error bodies, cache validators, links, the API root."""

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient

from features import Features
from main import create_app
from rest import (
    cache_control,
    href,
    install_error_handlers,
    json_response,
    link,
    page_links,
    problem_response,
    server_timing,
)


def test_href_and_link():
    assert href("kv-pairs", 3) == "/api/v1/kv-pairs/3"
    assert href("/assignments/", "q1") == "/api/v1/assignments/q1"
    assert link("/x", templated=True) == {"href": "/x", "templated": True}


def test_cache_control_values():
    assert cache_control(300, private=False) == "public, max-age=300"
    assert cache_control(0, private=True) == "private, no-cache"
    assert cache_control(0, private=False) == "public, no-cache"


def test_server_timing_format():
    assert server_timing("search", 12.345) == "search;dur=12.3"


def test_problem_response_shape():
    res = problem_response(404, "gone", errors=[1])
    assert res.status_code == 404 and res.media_type == "application/problem+json"
    assert res.body == (
        b'{"type":"about:blank","title":"Not Found","status":404,"detail":"gone","errors":[1]}'
    )


@pytest.mark.parametrize(
    "total,limit,offset,keys",
    [
        (5, 2, 0, {"self", "first", "last", "next"}),
        (5, 2, 2, {"self", "first", "last", "prev", "next"}),
        (5, 2, 4, {"self", "first", "last", "prev"}),
        (0, 2, 0, {"self", "first", "last"}),
    ],
)
def test_page_links(total, limit, offset, keys):
    links = page_links("/api/v1/things", {"status": "x"}, total, limit, offset)
    assert set(links) == keys
    assert links["last"]["href"] == "/api/v1/things?status=x&limit=2&offset=" + str(
        max(0, (total - 1) // limit * limit)
    )
    if "prev" in links:
        assert links["prev"]["href"].endswith(f"offset={max(0, offset - limit)}")


def _app():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/thing")
    def thing(request: Request):
        return json_response(request, {"a": 1}, max_age=60)

    @app.get("/private")
    def private(request: Request):
        return json_response(request, {"a": 1}, private=True)

    @app.get("/boom")
    def boom():
        raise HTTPException(status_code=409, detail="clash", headers={"X-Extra": "1"})

    @app.get("/typed/{n}")
    def typed(n: int):
        return n

    return app


@pytest.fixture
async def _plain():
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        yield c


async def test_json_response_sets_validators(_plain):
    res = await _plain.get("/thing")
    assert res.status_code == 200 and res.json() == {"a": 1}
    assert res.headers["cache-control"] == "public, max-age=60"
    assert res.headers["etag"].startswith('"') and len(res.headers["etag"]) == 34
    assert (await _plain.get("/private")).headers["cache-control"] == "private, no-cache"


@pytest.mark.parametrize("header", ["{etag}", "W/{etag}", '"other", {etag}', "*"])
async def test_if_none_match_gives_304(_plain, header):
    etag = (await _plain.get("/thing")).headers["etag"]
    res = await _plain.get("/thing", headers={"If-None-Match": header.format(etag=etag)})
    assert res.status_code == 304 and res.content == b""
    assert res.headers["etag"] == etag and res.headers["cache-control"] == "public, max-age=60"


async def test_stale_etag_gets_full_body(_plain):
    res = await _plain.get("/thing", headers={"If-None-Match": '"stale"'})
    assert res.status_code == 200 and res.json() == {"a": 1}


async def test_http_errors_are_problem_json_and_keep_headers(_plain):
    res = await _plain.get("/boom")
    assert res.status_code == 409 and res.headers["x-extra"] == "1"
    assert res.headers["content-type"] == "application/problem+json"
    assert res.json() == {
        "type": "about:blank",
        "title": "Conflict",
        "status": 409,
        "detail": "clash",
    }
    missing = await _plain.get("/nope")
    assert missing.status_code == 404 and missing.json()["title"] == "Not Found"


async def test_validation_errors_list_fields(_plain):
    res = await _plain.get("/typed/abc")
    body = res.json()
    assert res.status_code == 422 and body["detail"] == "Request validation failed"
    assert body["errors"][0]["field"] == "n"


async def test_api_root_lists_only_enabled_resources():
    def links(features):
        app = create_app(features, static_dir="")
        return app

    async def root(features):
        async with AsyncClient(
            transport=ASGITransport(app=links(features)), base_url="http://t"
        ) as c:
            res = await c.get("/api/v1")
            assert res.status_code == 200
            return res.json()["_links"]

    full = await root(Features())
    assert {"searches", "search-methods", "assignments", "kv-pairs", "benchmark"} <= set(full)
    assert full["searches"]["templated"] is True
    light = await root(Features(search=False, benchmark=False, kv_pairs=False))
    assert set(light) == {"self", "hadith", "annotators", "tokens", "assignments", "agreement"}
