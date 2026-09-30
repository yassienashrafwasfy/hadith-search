from fastapi import APIRouter

from features import Features
from rest import API_PREFIX, href, link

SEARCH_TEMPLATE = "{?q,method,lang,grade_filter,book_filter}"


def make_root_router(features: Features) -> APIRouter:
    """`GET /api/v1`: the entry point, listing only the resources this deployment serves."""
    router = APIRouter(tags=["root"])

    @router.get(API_PREFIX)
    def api_root():
        links = {
            "self": link(API_PREFIX),
            "hadith": link(href("hadiths", "{hadith_id}"), templated=True),
        }
        if features.search:
            links["search-methods"] = link(href("search-methods"))
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
