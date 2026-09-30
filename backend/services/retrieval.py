"""Retrieval systems as a registry of strategies over an injected `SearchContext`.

The ranking itself lives in `services.ranking` (SQL over PostgreSQL); this registry maps each
public slug to a ranking function and declares which feature flags it needs, so endpoints,
feature gating and dependencies are declared in one place.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from features import Features
from models import SearchRequest, SearchResponse
from services import ranking
from services.results import build_results

RERANK_CANDIDATES = 50
RERANK_TOP_K = 10
COSINE_TOP_K = 20

Scores = dict[int, float]


@dataclass(frozen=True)
class SearchContext:
    """A database session plus a lazy accessor for the E5 model."""

    session: Session
    model: Callable[[], Any]


@dataclass(frozen=True)
class RetrievalSystem:
    slug: str
    run: Callable[[SearchContext, str, str], Scores]
    requires: tuple[str, ...] = ()  # Features flags that must all be on

    def enabled(self, features: Features) -> bool:
        return features.search and all(features.is_enabled(flag) for flag in self.requires)


SYSTEMS: dict[str, RetrievalSystem] = {}


def _system(slug: str, *requires: str):
    def register(run):
        SYSTEMS[slug] = RetrievalSystem(slug, run, requires)
        return run

    return register


@_system("term-overlap")
def _term_overlap(ctx, query, lang):
    return ranking.term_overlap(ctx.session, query, lang)


@_system("tfidf")
def _tfidf(ctx, query, lang):
    return ranking.tf_idf(ctx.session, query, lang)


@_system("bm25")
def _bm25(ctx, query, lang):
    return ranking.bm25(ctx.session, query, lang)


@_system("bm25-tf-idf")
def _hybrid(ctx, query, lang):
    return ranking.bm25_tfidf_hybrid(ctx.session, query, lang)


@_system("bm25-prf")
def _bm25_prf(ctx, query, lang):
    return ranking.bm25_prf(ctx.session, query, lang)


@_system("semantic-rerank", "dense_retrieval")
def _semantic_rerank(ctx, query, lang):
    candidates = list(ranking.bm25(ctx.session, query, lang, limit=RERANK_CANDIDATES))
    return ranking.semantic_rerank(
        ctx.session, query, lang, candidates, ctx.model(), top_k=RERANK_TOP_K
    )


@_system("cosine-similarity", "dense_retrieval")
def _cosine(ctx, query, lang):
    return ranking.cosine_search(ctx.session, query, lang, ctx.model(), top_k=COSINE_TOP_K)


@_system("semantic-rrf", "dense_retrieval")
def _semantic_rrf(ctx, query, lang):
    return ranking.bm25_dense_rrf(ctx.session, query, lang, ctx.model())


@_system("cross-encoder-rerank", "cross_encoder")
def _cross_encoder(ctx, query, lang):
    return ranking.bm25_cross_encoder(ctx.session, query, lang)


@_system("final-pipeline", "dense_retrieval", "cross_encoder")
def _final_pipeline(ctx, query, lang):
    return ranking.final_pipeline(ctx.session, query, lang, ctx.model())


def enabled_systems(features: Features) -> list[RetrievalSystem]:
    return [system for system in SYSTEMS.values() if system.enabled(features)]


def run_search(system: RetrievalSystem, ctx: SearchContext, req: SearchRequest) -> SearchResponse:
    raw = system.run(ctx, req.query, req.lang.value.upper())
    results = build_results(ctx.session, raw, req.grade_filter, req.book_filter)
    return SearchResponse(number_of_results=len(results), results=results)
