"""Retrieval systems as a registry of strategies over an injected `SearchContext`.

Strangler-fig facade: the algorithms still live in the legacy `scripts.search` module (which
the offline pooling/evaluation scripts keep using); the API now reaches them only through
this registry, so endpoints, feature gating and dependencies are declared in one place.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from features import Features
from models import SearchRequest, SearchResponse
from scripts import (
    bm25,
    bm25_cross_encoder_rerank,
    bm25_semantic_rrf,
    bm25_tfidf_hybrid,
    bm25_with_expansion,
    final_search_pipeline,
    ranked_term_overlap,
    semantic_reranker,
    semantic_search_e5,
    tf_idf,
)
from services.results import build_results

RERANK_CANDIDATES = 50
RERANK_TOP_K = 10
COSINE_TOP_K = 20

Scores = dict[int, float]


@dataclass(frozen=True)
class SearchContext:
    """Everything a retrieval system may need, as lazy accessors (language is "EN" or "AR")."""

    inverted_index: Callable[[str], dict]
    embeddings: Callable[[str], Any]
    doc_lengths: Callable[[], dict]
    hadith_ids: Callable[[], Any]
    model: Callable[[], Any]
    hadiths_df: Callable[[], Any]
    get_hadith: Callable[[int], dict]


def default_search_context() -> SearchContext:
    """Context backed by the real indices, embeddings and model (loaded on first use)."""
    from scripts import (
        get_arabic_embeddings,
        get_arabic_inverted_index,
        get_document_lengths,
        get_english_embeddings,
        get_english_inverted_index,
        get_hadith,
        get_hadith_ids,
        get_hadiths_df,
        get_model,
    )

    by_language = {"EN": get_english_inverted_index, "AR": get_arabic_inverted_index}
    embeddings = {"EN": get_english_embeddings, "AR": get_arabic_embeddings}
    return SearchContext(
        inverted_index=lambda lang: by_language[lang](),
        embeddings=lambda lang: embeddings[lang](),
        doc_lengths=get_document_lengths,
        hadith_ids=get_hadith_ids,
        model=get_model,
        hadiths_df=get_hadiths_df,
        get_hadith=get_hadith,
    )


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
    return ranked_term_overlap(query, lang, ctx.inverted_index(lang))


@_system("tfidf")
def _tfidf(ctx, query, lang):
    return tf_idf(query, lang, ctx.inverted_index(lang), ctx.doc_lengths())


@_system("bm25")
def _bm25(ctx, query, lang):
    return bm25(query, lang, ctx.inverted_index(lang), ctx.doc_lengths())


@_system("bm25-tf-idf")
def _hybrid(ctx, query, lang):
    return bm25_tfidf_hybrid(query, lang, ctx.inverted_index(lang), ctx.doc_lengths())


@_system("bm25-prf")
def _bm25_prf(ctx, query, lang):
    return bm25_with_expansion(
        query, lang, ctx.inverted_index(lang), ctx.doc_lengths(), ctx.get_hadith
    )


@_system("semantic-rerank", "dense_retrieval")
def _semantic_rerank(ctx, query, lang):
    candidates = list(_bm25(ctx, query, lang))[:RERANK_CANDIDATES]
    return semantic_reranker(
        query,
        lang,
        candidates,
        ctx.model(),
        ctx.embeddings(lang),
        ctx.hadith_ids(),
        top_k=RERANK_TOP_K,
    )


@_system("cosine-similarity", "dense_retrieval")
def _cosine(ctx, query, lang):
    return semantic_search_e5(
        query, lang, ctx.model(), ctx.embeddings(lang), ctx.hadith_ids(), top_k=COSINE_TOP_K
    )


@_system("semantic-rrf", "dense_retrieval")
def _semantic_rrf(ctx, query, lang):
    return bm25_semantic_rrf(
        query,
        lang,
        ctx.inverted_index(lang),
        ctx.doc_lengths(),
        ctx.embeddings(lang),
        ctx.hadith_ids(),
        ctx.model(),
    )


@_system("cross-encoder-rerank", "cross_encoder")
def _cross_encoder(ctx, query, lang):
    return bm25_cross_encoder_rerank(
        query, lang, ctx.inverted_index(lang), ctx.doc_lengths(), ctx.hadiths_df()
    )


@_system("final-pipeline", "dense_retrieval", "cross_encoder")
def _final_pipeline(ctx, query, lang):
    text_column = "English_Text" if lang == "EN" else "Arabic_Text"
    return final_search_pipeline(
        query=query,
        language=lang,
        index=ctx.inverted_index(lang),
        doc_lengths=ctx.doc_lengths(),
        embeddings=ctx.embeddings(lang),
        hadith_ids=ctx.hadith_ids(),
        model=ctx.model(),
        eval_ids=set(map(int, ctx.hadith_ids())),
        texts_dict=ctx.hadiths_df()[text_column].dropna().astype(str).to_dict(),
    )


def enabled_systems(features: Features) -> list[RetrievalSystem]:
    return [system for system in SYSTEMS.values() if system.enabled(features)]


def run_search(system: RetrievalSystem, ctx: SearchContext, req: SearchRequest) -> SearchResponse:
    started = time.perf_counter()
    raw = system.run(ctx, req.query, req.lang.value.upper())
    results = build_results(raw, ctx.hadiths_df(), req.grade_filter, req.book_filter)
    elapsed_ms = (time.perf_counter() - started) * 1000
    return SearchResponse(
        number_of_results=len(results), results=results, response_time_ms=round(elapsed_ms, 2)
    )
