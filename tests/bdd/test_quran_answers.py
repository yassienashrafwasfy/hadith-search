import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pytest_bdd import given, scenarios, then, when

from features import Features
from limiter import SearchLimiter
from quran.types import FALLBACK_SENTENCE, Answer, Citation
from rest import install_error_handlers
from routers.quran import get_quran_agent, make_quran_router

scenarios("../../docs/behaviours/quran_answers.feature")

URL = "/api/v1/answers"


@pytest.fixture
def _quran(_run):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(make_quran_router(Features(quran_rag=True)))
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield app, client
    _run(client.aclose())


def _agent(app, fn):
    app.dependency_overrides[get_quran_agent] = lambda: fn


@given("the Quran agent answers with one citation")
def _answers(_quran):
    async def agent(question):
        return Answer("جواب", [Citation("quran:1:1", "quran", "نص الآية")], ["finalize"])

    _agent(_quran[0], agent)


@given("the Quran agent crashes")
def _crashes(_quran):
    async def agent(question):
        raise RuntimeError("down")

    _agent(_quran[0], agent)


@given("the Quran answer queue is full")
def _full(_quran, _run):
    app = _quran[0]
    app.state.quran_limiter = SearchLimiter(1, 0, 1)

    async def hold():
        await app.state.quran_limiter._slots.acquire()

    _run(hold())
    _agent(app, lambda q: asyncio.sleep(0))


@when("a question is posted")
def _post(_quran, _ctx, _run):
    _ctx["responses"] = [_run(_quran[1].post(URL, json={"question": "ما معنى الصبر؟"}))]


@when("an empty question is posted")
def _post_empty(_quran, _ctx, _run):
    _ctx["responses"] = [_run(_quran[1].post(URL, json={"question": ""}))]


@then("the answer carries the citation text and a trace")
def _carries(_ctx):
    body = _ctx["responses"][0].json()
    assert body["citations"][0]["text"] == "نص الآية" and body["trace"] == ["finalize"]


@then("the answer is the fallback sentence")
def _fallback(_ctx):
    body = _ctx["responses"][0].json()
    assert body["answer"] == FALLBACK_SENTENCE and body["fallback"] is True
