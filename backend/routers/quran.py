"""`POST /api/v1/answers`: a grounded answer to a Quran question, with citations and a trace.

Generation is slow and CPU bound (a local llama-server), so the route has its own small queue
(`app.state.quran_limiter`, sized by the `quran_*` settings) and is never cached. The agent is
reached through the `get_quran_agent` dependency; tests override it with a fake.
"""

import asyncio
import json
import logging
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from features import Features
from inputs import Text
from limiter import Overloaded, SearchLimiter
from quran.types import FALLBACK_SENTENCE, Answer
from rest import API_PREFIX, href, link
from settings import get_settings

logger = logging.getLogger(__name__)

QuranAgent = Callable[..., Awaitable[Answer]]  # (question, on_step=None)


class AnswerRequest(BaseModel):
    question: Annotated[Text, Field(min_length=1, max_length=500)]


RETRIEVE_K = 8
MERGE_FRACTION = 0.5


def _tafsir_of_named_verse(session, source: str, vector, question: str) -> list:
    """For a tafsir source: find the verse the question names in the Quran text, return its tafsir."""
    from quran.db import list_sources
    from quran.retrieval import get_passage, search_source
    from quran.types import Filters

    verses = next((s.name for s in list_sources(session) if not s.authors), None)
    if verses is None or verses == source:
        return []
    anchor = search_source(session, verses, vector, Filters(), 1, question)
    if not anchor or anchor[0].surah is None:
        return []
    top = anchor[0]
    return get_passage(session, source, top.surah, top.ayah_from, top.ayah_to)


def _with_named_surah(filters, question: str):
    """A surah named in the question ("سورة الإخلاص") becomes a filter; no keyword gate then."""
    from dataclasses import replace

    from quran.surahs import find_surah
    from quran.types import Filters

    found = find_surah(question)
    if found is None:
        return filters, question
    return replace(filters or Filters(), surah=found[0]), None


def _retrieve_sync(encoder, source: str, question: str, filters) -> list:
    """Embed the question, search one source, merge ayat into their passage (blocking)."""
    from database import get_sync_session
    from quran.retrieval import auto_merge, search_source
    from scripts.arabic_encoder import encoding_text

    vector = [float(x) for x in encoder.encode([encoding_text(question)])[0]]
    with get_sync_session() as session:
        named = _tafsir_of_named_verse(session, source, vector, question)
        if named:
            return named
        filters, query = _with_named_surah(filters, question)
        hits = search_source(session, source, vector, filters, RETRIEVE_K, query)
        return auto_merge(session, hits, MERGE_FRACTION)


def _sources_sync() -> list:
    from database import get_sync_session
    from quran.db import list_sources

    with get_sync_session() as session:
        return list_sources(session)


async def get_quran_agent() -> AsyncIterator[QuranAgent]:
    """FastAPI dependency: the agent that answers one question. Override in tests."""
    import httpx

    from quran.agent import Deps, answer_question
    from quran.llm import LlamaServerClient
    from quran.web import AllowListedWeb
    from scripts import get_model

    settings = get_settings()
    timeout = settings.quran_llm_timeout_seconds
    controller = LlamaServerClient(settings.quran_controller_url, timeout)
    answerer = LlamaServerClient(settings.quran_answerer_url, timeout)
    web_client = httpx.AsyncClient() if settings.quran_web_enabled else None

    reranker = None
    if settings.quran_rerank_enabled:
        from services.reranker import CrossEncoderReranker

        reranker = CrossEncoderReranker(settings.reranker_model)

    async def retrieve(source: str, question: str, filters) -> list:
        return await asyncio.to_thread(_retrieve_sync, get_model(), source, question, filters)

    async def run(question: str, on_step=None) -> Answer:
        deps = Deps(
            controller=controller,
            answerer=answerer,
            retrieve=retrieve,
            sources=await asyncio.to_thread(_sources_sync),
            web=AllowListedWeb(web_client).search if web_client else None,
            max_loops=settings.quran_max_loops,
            rerank=reranker,
            rerank_candidates=settings.quran_rerank_candidates,
            rerank_min_score=settings.quran_rerank_min_score,
            on_step=on_step,
        )
        return await answer_question(question, deps)

    try:
        yield run
    finally:
        await controller.aclose()
        await answerer.aclose()
        if web_client:
            await web_client.aclose()


async def quran_slot(request: Request) -> AsyncIterator[None]:
    """Hold a slot of the answer queue for the whole request, or answer 503 with Retry-After."""
    limiter: SearchLimiter | None = getattr(request.app.state, "quran_limiter", None)
    if limiter is None:
        yield
        return
    try:
        async with limiter.slot():
            yield
    except Overloaded as exc:
        logger.warning("answer rejected: %s (%d waiting)", exc, limiter.waiting)
        raise HTTPException(
            status_code=503,
            detail="The server is busy. Try again in a moment.",
            headers={"Retry-After": str(math.ceil(limiter.timeout))},
        ) from None


def _fallback(reason: str) -> Answer:
    return Answer(text=FALLBACK_SENTENCE, trace=[reason], fallback=True)


def make_quran_router(features: Features) -> APIRouter:
    router = APIRouter(prefix=API_PREFIX, tags=["quran"])

    def _payload(answer: Answer) -> dict:
        return {
            "answer": answer.text,
            "fallback": answer.fallback,
            "citations": [asdict(c) for c in answer.citations],
            "trace": answer.trace,
            "_links": {"self": link(href("answers")), "root": link(API_PREFIX)},
        }

    async def _run(agent: QuranAgent, question: str, **kwargs) -> Answer:
        try:
            return await agent(question, **kwargs)
        except Exception:
            logger.exception("quran agent failed")
            return _fallback("agent error")

    async def _events(agent: QuranAgent, question: str) -> AsyncIterator[str]:
        """NDJSON: one {"step": name} line per agent step that takes long, then {"answer": ...}."""
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(
            _run(agent, question, on_step=lambda name: queue.put_nowait(name))
        )
        task.add_done_callback(lambda _: queue.put_nowait(None))
        while (name := await queue.get()) is not None:
            yield json.dumps({"step": name}) + "\n"
        yield json.dumps({"answer": _payload(task.result())}, ensure_ascii=False) + "\n"

    @router.post("/answers", dependencies=[Depends(quran_slot)])
    async def create_answer(
        body: AnswerRequest, stream: bool = False, agent: QuranAgent = Depends(get_quran_agent)
    ):
        headers = {"Cache-Control": "no-store"}
        if stream:
            return StreamingResponse(
                _events(agent, body.question), media_type="application/x-ndjson", headers=headers
            )
        return JSONResponse(_payload(await _run(agent, body.question)), headers=headers)

    return router
