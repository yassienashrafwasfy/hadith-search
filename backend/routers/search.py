from fastapi import APIRouter, Depends

from features import Features
from models import SearchRequest, SearchResponse
from services.retrieval import (
    RetrievalSystem,
    SearchContext,
    default_search_context,
    enabled_systems,
    run_search,
)


def get_search_context() -> SearchContext:
    """FastAPI dependency; override in tests to inject fake indices/models."""
    return default_search_context()


def _endpoint(system: RetrievalSystem):
    def search(req: SearchRequest, ctx: SearchContext = Depends(get_search_context)):
        return run_search(system, ctx, req)

    search.__name__ = f"search_{system.slug.replace('-', '_')}"
    return search


def make_search_router(features: Features) -> APIRouter:
    """One POST /search/<slug> per retrieval system that the feature flags allow."""
    router = APIRouter()
    for system in enabled_systems(features):
        router.add_api_route(
            f"/search/{system.slug}",
            _endpoint(system),
            methods=["POST"],
            response_model=SearchResponse,
        )
    return router
