"""More exact-value assertions for limiter, rest and startup (found by mutmut survivors)."""

import asyncio
import logging

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, create_model

import startup
from features import Features
from limiter import Overloaded, SearchLimiter, search_slot
from rest import (
    EncodedJson,
    _etag_matches,
    href,
    install_error_handlers,
    json_response,
    page_links,
)


async def _get(app, path="/", **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.get(path, **kwargs)


# ---- limiter -------------------------------------------------------------------------------


async def test_overload_reasons_are_named():
    full = SearchLimiter(1, 0, 1)
    async with full.slot():
        with pytest.raises(Overloaded, match=r"^queue full$"):
            async with full.slot():
                pass
    slow = SearchLimiter(1, 1, 0.01)
    async with slow.slot():
        with pytest.raises(Overloaded, match=r"^waited too long$"):
            async with slow.slot():
                pass


async def test_the_503_body_header_and_log_line(caplog):
    app = FastAPI()
    install_error_handlers(app)
    app.state.search_limiter = SearchLimiter(1, 0, 1.2)
    gate = asyncio.Event()

    @app.get("/work", dependencies=[Depends(search_slot)])
    async def work():
        await gate.wait()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        running = asyncio.create_task(client.get("/work"))
        await asyncio.sleep(0.05)
        with caplog.at_level(logging.WARNING, logger="limiter"):
            refused = await client.get("/work")
        gate.set()
        await running
    assert refused.json()["detail"] == "The server is busy. Try again in a moment."
    assert refused.headers["retry-after"] == "2"
    assert [r.getMessage() for r in caplog.records] == ["search rejected: queue full (0 waiting)"]


def test_href_strips_only_slashes_from_each_part():
    assert href("Xkv/", "/X1") == "/api/v1/Xkv/X1"


# ---- rest ----------------------------------------------------------------------------------


def test_page_links_with_a_zero_limit_points_last_at_offset_zero():
    links = page_links("/p", {}, 10, 0, 0)
    assert links["last"]["href"] == "/p?limit=0&offset=0"


def test_page_links_are_exact_in_the_middle_and_at_the_edge():
    links = page_links("/p", {"q": "a"}, 10, 3, 3)
    assert links["self"]["href"] == "/p?q=a&limit=3&offset=3"
    assert links["first"]["href"] == "/p?q=a&limit=3&offset=0"
    assert links["prev"]["href"] == "/p?q=a&limit=3&offset=0"
    assert links["next"]["href"] == "/p?q=a&limit=3&offset=6"
    assert links["last"]["href"] == "/p?q=a&limit=3&offset=9"
    # the next page would start exactly at `total`, so there is none
    assert "next" not in page_links("/p", {}, 6, 3, 3)
    assert page_links("/p", {}, 7, 3, 3)["next"]["href"] == "/p?limit=3&offset=6"


async def test_401_adds_a_www_authenticate_bearer_header_but_keeps_a_given_one():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/plain")
    async def plain():
        raise HTTPException(401, "no")

    @app.get("/custom")
    async def custom():
        raise HTTPException(401, "no", headers={"WWW-Authenticate": "Basic"})

    plain_res = await _get(app, "/plain")
    assert plain_res.headers["www-authenticate"] == "Bearer"
    assert (await _get(app, "/custom")).headers["www-authenticate"] == "Basic"


async def test_validation_errors_name_nested_fields_and_messages():
    class Inner(BaseModel):
        n: int

    Outer = create_model("Outer", inner=(Inner, ...))

    app = FastAPI()
    install_error_handlers(app)

    @app.post("/x")
    async def post(body: Outer):
        return body

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        res = await client.post("/x", json={"inner": {"n": "abc"}})
    (error,) = res.json()["errors"]
    assert error["field"] == "inner.n"
    assert set(error) == {"field", "message"} and "integer" in error["message"]


@pytest.mark.parametrize(
    "header,expected",
    [
        ('"a","b"', True),
        ('"x", W/"b"', True),
        ('"x" "b"', False),
        ("*", True),
        (None, False),
    ],
)
def test_etag_matching(header, expected):
    assert _etag_matches(header, '"b"') is expected


async def test_json_response_bodies_headers_and_conditional_get():
    app = FastAPI()

    @app.get("/obj")
    async def obj(request: Request):
        return json_response(request, {"a": [1, 2], "t": "حديث"}, max_age=5)

    @app.get("/pre")
    async def pre(request: Request):
        return json_response(request, EncodedJson('{"x":1}'))

    res = await _get(app, "/obj")
    assert res.content == '{"a":[1,2],"t":"حديث"}'.encode()
    assert res.headers["content-type"] == "application/json"
    assert res.headers["cache-control"] == "public, max-age=5"
    assert (await _get(app, "/pre")).content == b'{"x":1}'
    again = await _get(app, "/obj", headers={"If-None-Match": res.headers["etag"]})
    assert again.status_code == 304 and again.content == b""
    assert again.headers["etag"] == res.headers["etag"]


# ---- startup -------------------------------------------------------------------------------


def test_run_step_prints_label_then_done_on_one_line(capsys):
    startup._run_step("thing", lambda: None)
    assert capsys.readouterr().out == "  - thing... done\n"


def test_empty_index_warning_text_has_no_newline(_patched_paths, capsys):
    startup.check_index()
    assert capsys.readouterr().out == (
        "WARNING: no postings in the database; searches will be empty. "
    )


def test_step_labels_are_exact():
    assert [label for label, _ in startup._index_steps()] == ["Search index in PostgreSQL"]
    features = Features(dense_retrieval=True, eager_model=True)
    assert [label for label, _ in startup._model_steps(features)] == [
        "Arabic sentence encoder (ONNX)"
    ]


def test_preload_messages_and_arguments_are_exact(monkeypatch, capsys):
    seen = []
    features = Features(dense_retrieval=True, eager_model=False)
    monkeypatch.setattr(startup, "_index_steps", lambda: [("idx", lambda: None)])

    def _models(given):
        seen.append(given)
        return []

    monkeypatch.setattr(startup, "_model_steps", _models)
    startup.preload_resources(features)
    assert seen == [features]
    assert capsys.readouterr().out == (
        "Checking the search index and loading models at startup...\n"
        "  - idx... done\n"
        "Dense retrieval enabled: lazy-loading the Arabic encoder on first request\n"
    )
    startup.preload_resources(Features(search=False))
    assert capsys.readouterr().out == "Search disabled: skipping model/index preload\n"
