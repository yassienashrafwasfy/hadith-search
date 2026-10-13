import asyncio

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from features import Features
from limiter import SearchLimiter
from main import create_app
from quran.types import FALLBACK_SENTENCE, Answer, Citation
from rest import install_error_handlers
from routers.quran import get_quran_agent, make_quran_router
from settings import Settings

URL = "/api/v1/answers"
ANSWER = Answer(
    text="الجواب",
    citations=[Citation(ref="quran:1:1", source="quran", text="بِسْمِ اللَّهِ")],
    trace=["route", "finalize"],
)


def _app(agent, limiter=None):
    app = FastAPI()
    install_error_handlers(app)
    app.state.quran_limiter = limiter
    app.include_router(make_quran_router(Features(quran_rag=True)))
    app.dependency_overrides[get_quran_agent] = lambda: agent
    return app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _ok(question):
    return ANSWER


async def test_answer_has_text_citations_and_trace():
    async with _client(_app(_ok)) as c:
        res = await c.post(URL, json={"question": "ما هي الفاتحة؟"})
    body = res.json()
    assert res.status_code == 200 and res.headers["cache-control"] == "no-store"
    assert body["answer"] == "الجواب" and body["fallback"] is False
    assert body["citations"][0]["text"] == "بِسْمِ اللَّهِ" and body["trace"] == [
        "route",
        "finalize",
    ]


async def test_bad_input_is_422_problem_json():
    async with _client(_app(_ok)) as c:
        for body in ({}, {"question": ""}, {"question": "a" * 501}, {"question": "a\x00b"}):
            res = await c.post(URL, json=body)
            assert res.status_code == 422
            assert res.headers["content-type"].startswith("application/problem+json")


async def test_agent_crash_gives_the_fallback_sentence():
    async def boom(question):
        raise RuntimeError("x")

    async with _client(_app(boom)) as c:
        res = await c.post(URL, json={"question": "سؤال"})
    assert res.status_code == 200
    assert res.json()["answer"] == FALLBACK_SENTENCE and res.json()["fallback"] is True


async def test_full_queue_is_503_with_retry_after():
    gate = asyncio.Event()

    async def slow(question):
        await gate.wait()
        return ANSWER

    app = _app(slow, SearchLimiter(1, 0, 2.2))
    async with _client(app) as c:
        running = asyncio.create_task(c.post(URL, json={"question": "a"}))
        await asyncio.sleep(0.05)
        refused = await c.post(URL, json={"question": "b"})
        assert refused.status_code == 503 and refused.headers["retry-after"] == "3"
        assert refused.headers["content-type"].startswith("application/problem+json")
        gate.set()
        assert (await running).status_code == 200


def test_route_only_when_the_flag_is_on():
    on = create_app(Features(quran_rag=True), static_dir="", settings=Settings())
    off = create_app(Features(quran_rag=False), static_dir="", settings=Settings())
    assert URL in {r.path for r in on.routes} and URL not in {r.path for r in off.routes}
    assert on.state.quran_limiter is not None and not hasattr(off.state, "quran_limiter")


async def test_root_links_answers_only_when_enabled():
    for flag in (True, False):
        app = create_app(Features(quran_rag=flag), static_dir="", settings=Settings())
        async with _client(app) as c:
            links = (await c.get("/api/v1")).json()["_links"]
        assert ("answers" in links) is flag


def test_a_tafsir_question_that_names_a_verse_returns_that_verses_tafsir(
    _quran_session, _fake_encoder, _mini_corpus
):
    from quran_support import _simple

    from routers.quran import _tafsir_of_named_verse

    texts = {(a.surah, a.ayah): _simple(a.text).split() for a in _mini_corpus if a.surah == 112}

    def unique(key):
        rest = {w for k, ws in texts.items() if k != key for w in ws}
        return [w for w in texts[key] if len(w) >= 3 and w not in rest]

    key, word = next((k, unique(k)[0]) for k in texts if unique(k))
    vector = [float(x) for x in _fake_encoder.encode([word])[0]]

    found = _tafsir_of_named_verse(_quran_session, "tafsir_mini", vector, f"تفسير {word}")

    assert found and {(n.surah, n.ayah_from) for n in found} == {key}
    assert all(n.source == "tafsir_mini" for n in found)
    assert _tafsir_of_named_verse(_quran_session, "quran", vector, word) == []  # not a tafsir
    assert _tafsir_of_named_verse(_quran_session, "tafsir_mini", vector, "كيف أطبخ الأرز") == []


async def test_stream_sends_steps_then_the_answer():
    import json

    async def agent(question, on_step=None):
        on_step("web_search")
        return ANSWER

    async with _client(_app(agent)) as c:
        res = await c.post(URL + "?stream=true", json={"question": "ما هي الفاتحة؟"})
    lines = [json.loads(line) for line in res.text.splitlines()]
    assert res.headers["content-type"].startswith("application/x-ndjson")
    assert lines[0] == {"step": "web_search"} and lines[1]["answer"]["answer"] == "الجواب"
