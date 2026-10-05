"""Retrieval systems as a registry of strategies over an injected `SearchContext`.

The ranking itself lives in `services.ranking` (SQL over PostgreSQL); this registry maps each
public slug to a ranking function and declares which feature flags it needs, so endpoints,
feature gating and dependencies are declared in one place.
"""

from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.orm import Session

from features import Features
from models import SearchRequest, SearchResponse
from services import ranking
from services.results import allowed_ids, build_results
from services.suggestions import did_you_mean

RERANK_CANDIDATES = 50
RERANK_TOP_K = 10
COSINE_TOP_K = 20

Scores = dict[int, float]


@dataclass(frozen=True)
class SearchContext:
    """A database session plus a lazy accessor for the Arabic sentence encoder."""

    session: Session
    model: Callable[[], Any]
    restrict: Collection[int] | None = None  # ids that pass the filters; the dense systems use it


@dataclass(frozen=True)
class RetrievalSystem:
    slug: str
    run: Callable[[SearchContext, str, str], Scores]
    requires: tuple[str, ...] = ()  # Features flags that must all be on
    languages: tuple[str, ...] = ("EN", "AR")  # query languages the system can answer
    keyword: bool = False  # matches words, so an empty result may be a typo (`did_you_mean`)

    def enabled(self, features: Features) -> bool:
        return features.search and all(features.is_enabled(flag) for flag in self.requires)


SYSTEMS: dict[str, RetrievalSystem] = {}


def _system(
    slug: str, *requires: str, languages: tuple[str, ...] = ("EN", "AR"), keyword: bool = False
):
    def register(run):
        SYSTEMS[slug] = RetrievalSystem(slug, run, requires, languages, keyword)
        return run

    return register


# The dense model only knows Arabic, so the dense systems answer Arabic queries only.
_DENSE = {"languages": ("AR",)}


@_system("term-overlap")
def _term_overlap(ctx, query, lang):
    return ranking.term_overlap(ctx.session, query, lang)


@_system("tfidf")
def _tfidf(ctx, query, lang):
    return ranking.tf_idf(ctx.session, query, lang)


@_system("bm25", keyword=True)
def _bm25(ctx, query, lang):
    return ranking.bm25(ctx.session, query, lang)


@_system("bm25-tf-idf")
def _hybrid(ctx, query, lang):
    return ranking.bm25_tfidf_hybrid(ctx.session, query, lang)


@_system("bm25-prf")
def _bm25_prf(ctx, query, lang):
    return ranking.bm25_prf(ctx.session, query, lang)


@_system("semantic-rerank", "dense_retrieval", **_DENSE)
def _semantic_rerank(ctx, query, lang):
    candidates = list(
        ranking.bm25(ctx.session, query, lang, restrict=ctx.restrict, limit=RERANK_CANDIDATES)
    )
    return ranking.semantic_rerank(
        ctx.session, query, lang, candidates, ctx.model(), top_k=RERANK_TOP_K
    )


@_system("cosine-similarity", "dense_retrieval", **_DENSE)
def _cosine(ctx, query, lang):
    return ranking.cosine_search(
        ctx.session, query, lang, ctx.model(), top_k=COSINE_TOP_K, restrict=ctx.restrict
    )


@_system("semantic-rrf", "dense_retrieval", **_DENSE)
def _semantic_rrf(ctx, query, lang):
    return ranking.bm25_dense_rrf(ctx.session, query, lang, ctx.model(), restrict=ctx.restrict)


@_system("exact", keyword=True)
def _exact(ctx, query, lang):
    return ranking.exact_search(ctx.session, query, lang)


@_system("exact-semantic-rrf", "dense_retrieval", **_DENSE)
def _exact_semantic_rrf(ctx, query, lang):
    return ranking.exact_dense_rrf(ctx.session, query, lang, ctx.model(), restrict=ctx.restrict)


def enabled_systems(features: Features) -> list[RetrievalSystem]:
    return [system for system in SYSTEMS.values() if system.enabled(features)]


def run_search(system: RetrievalSystem, ctx: SearchContext, req: SearchRequest) -> SearchResponse:
    # Filters apply before the dense systems cut to their top results, so a filtered search still
    # returns a full page; the lexical systems rank everything and are filtered in build_results.
    if "dense_retrieval" in system.requires:
        allowed = allowed_ids(ctx.session, req.grade_filter, req.book_filter)
        ctx = replace(ctx, restrict=allowed)
    raw = system.run(ctx, req.query, req.lang.value.upper())
    results = build_results(
        ctx.session, raw, req.grade_filter, req.book_filter, allowed=ctx.restrict
    )
    hint = None
    if system.keyword and not results:
        hint = did_you_mean(ctx.session, req.query, req.lang.value.upper())
    return SearchResponse(number_of_results=len(results), results=results, did_you_mean=hint)
