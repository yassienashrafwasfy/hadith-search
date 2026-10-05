import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select

from database import get_session
from features import Features
from rest import API_PREFIX, href, link, problem_response

HEALTH_TIMEOUT_SECONDS = 3.0

SEARCH_TEMPLATE = "{?q,method,lang,grade_filter,book_filter}"


def make_root_router(features: Features) -> APIRouter:
    """`GET /api/v1`: the entry point, listing only the resources this deployment serves."""
    router = APIRouter(tags=["root"])

    @router.get(href("health"))
    async def health():
        """Liveness and readiness in one cheap call: the process answers and the database does too.

        No authentication, no search queue slot, and never cached. The body says nothing about
        versions, settings or the database address.
        """
        try:
            async with get_session() as session:
                await asyncio.wait_for(session.execute(select(1)), HEALTH_TIMEOUT_SECONDS)
        except Exception:
            return problem_response(
                503, "The database is not reachable.", headers={"Cache-Control": "no-store"}
            )
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    @router.get(API_PREFIX)
    def api_root():
        links = {
            "self": link(API_PREFIX),
            "health": link(href("health")),
            "hadith": link(href("hadiths", "{hadith_id}"), templated=True),
        }
        if features.search:
            links["search-methods"] = link(href("search-methods"))
            links["suggestions"] = link(href("suggestions") + "{?q,limit}", templated=True)
            links["searches"] = link(href("searches") + SEARCH_TEMPLATE, templated=True)
        if features.annotation:
            links["annotators"] = link(href("annotators"))
            links["tokens"] = link(href("tokens"))
            links["assignments"] = link(href("assignments"))
            links["agreement"] = link(href("agreement"))
        if features.kv_pairs:
            links["kv-pairs"] = link(href("kv-pairs"))
        if features.benchmark:
            links["benchmark"] = link(href("benchmark"))
        return {"_links": links}

    return router
