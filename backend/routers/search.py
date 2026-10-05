import json
import time
from collections.abc import Iterator
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request

import timing
from database import get_sync_session
from features import Features
from inputs import Text
from limiter import search_slot
from models import Lang, SearchRequest
from rest import API_PREFIX, EncodedJson, href, json_response, link, server_timing
from services.retrieval import SearchContext, enabled_systems, run_search

CACHE_SECONDS = 300  # results only change when the indices are rebuilt


def get_search_context() -> Iterator[SearchContext]:
    """FastAPI dependency: one database session per request. Override in tests to fake the model."""
    from scripts import get_model

    with get_sync_session() as session:
        yield SearchContext(session=session, model=get_model)


def make_search_router(features: Features) -> APIRouter:
    """`GET /searches` and `GET /search-methods`, limited to the systems the flags allow."""
    router = APIRouter(prefix=API_PREFIX, tags=["search"])
    systems = {system.slug: system for system in enabled_systems(features)}

    @router.get("/search-methods")
    def list_search_methods(request: Request):
        methods = [
            {
                "slug": slug,
                "languages": [language.lower() for language in system.languages],
                "_links": {
                    "search": link(f"{href('searches')}?method={slug}&q={{q}}", templated=True)
                },
            }
            for slug, system in systems.items()
        ]
        body = {"methods": methods, "_links": {"self": link(href("search-methods"))}}
        return json_response(request, body, max_age=CACHE_SECONDS)

    @router.get("/searches", dependencies=[Depends(search_slot)])
    def search(
        request: Request,
        q: Annotated[Text, Query(min_length=1, max_length=500)],
        method: str,
        lang: Lang = Lang.en,
        grade_filter: Text | None = None,
        book_filter: Text | None = None,
        ctx: SearchContext = Depends(get_search_context),
    ):
        system = systems.get(method)
        if system is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown method '{method}'. Available: {', '.join(systems)}",
            )
        if lang.value.upper() not in system.languages:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Method '{method}' supports only: "
                    f"{', '.join(language.lower() for language in system.languages)}"
                ),
            )
        timings = timing.start()
        started = time.perf_counter()
        response = run_search(
            system,
            ctx,
            SearchRequest(query=q, lang=lang, grade_filter=grade_filter, book_filter=book_filter),
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        params = {"q": q, "method": method, "lang": lang.value}
        params.update(
            {k: v for k, v in (("grade_filter", grade_filter), ("book_filter", book_filter)) if v}
        )
        links = {
            "self": link(f"{href('searches')}?{urlencode(params)}"),
            "methods": link(href("search-methods")),
        }
        # The model is serialised once by pydantic; the links are appended to its JSON object.
        body = EncodedJson(
            f'{response.model_dump_json()[:-1]},"_links":'
            f"{json.dumps(links, ensure_ascii=False, separators=(',', ':'))}}}"
        )
        return json_response(
            request,
            body,
            max_age=CACHE_SECONDS,
            headers={
                "Server-Timing": ", ".join(
                    [server_timing("search", elapsed_ms), *timing.metrics(timings)]
                )
            },
        )

    return router
