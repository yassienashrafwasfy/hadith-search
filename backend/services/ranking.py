"""Ranking over PostgreSQL: BM25 and TF-IDF from the postings tables, dense search with pgvector.

Every function takes a sync SQLAlchemy `Session` and returns `{hadith_id: score}` ordered best
first, the same shape the old in-memory code returned. The BM25 and TF-IDF formulas are the ones
the pickled index used, now evaluated in SQL (see `_bm25_statement`). `restrict` limits a search
to a set of hadith ids; the evaluation pipeline uses it for its judged pool.
"""

import threading
import time
from collections import Counter
from collections.abc import Collection
from functools import lru_cache
from math import log

import numpy as np
from sqlalchemy import (
    ARRAY,
    Float,
    Integer,
    Numeric,
    Select,
    String,
    and_,
    any_,
    case,
    cast,
    func,
    literal,
    select,
)
from sqlalchemy.orm import Session

import timing
from models import Hadith, HadithEmbedding, HadithLength, HadithPreprocessed, Posting, Term
from models.embedding_sets import embedding_table
from scripts.arabic_encoder import encoding_text
from scripts.preprocess import preprocess_arabic, preprocess_english
from scripts.search import rrf_fusion
from settings import get_settings

K1 = 1.2
B = 0.75
PRF_DOCS = 5
PRF_TERMS = 3
PRF_ALPHA = 1.0
PRF_BETA = 0.5
HYBRID_ALPHA = 0.8

Scores = dict[int, float]
Ids = Collection[int] | Select  # hadith ids, or a select() of them (results.allowed_query)


def preprocess_query(query: str, lang: str) -> str:
    return preprocess_arabic(query) if lang == "AR" else preprocess_english(query)


def _length_column(lang: str):
    return HadithLength.arabic_len if lang == "AR" else HadithLength.english_len


STATS_TTL_SECONDS = 300
_stats_cache: dict[tuple[str, str], tuple[float, tuple[int, float]]] = {}
_stats_lock = threading.Lock()


def clear_corpus_stats() -> None:
    """Forget cached corpus statistics (after the corpus or the index tables were rewritten)."""
    with _stats_lock:
        _stats_cache.clear()


def _corpus_stats(session: Session, lang: str) -> tuple[int, float]:
    """(number of documents, average document length in the language).

    Cached per database for STATS_TTL_SECONDS: BM25 and TF-IDF ask for it up to three times per
    search, and it only changes when the corpus is rebuilt (another process, so the time limit
    is what keeps a running server current).
    """
    key = (session.get_bind().url.render_as_string(hide_password=True), lang)
    now = time.monotonic()
    with _stats_lock:
        hit = _stats_cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    n_docs, average = session.execute(
        select(func.count(), func.avg(_length_column(lang))).select_from(HadithLength)
    ).one()
    stats = (n_docs, float(average or 0))
    with _stats_lock:
        _stats_cache[key] = (now + STATS_TTL_SECONDS, stats)
    return stats


def _query_terms(session: Session, lang: str, weights: dict[str, float]):
    """(df, qtf, where) for the query terms the index knows, or None when it knows none.

    `df` and `qtf` are CASE expressions on `postings.term` (the term's document frequency and
    query weight), `where` selects the terms' postings with the terms as one array literal.
    The ranking statements used to join a VALUES list of the terms to `terms` and then to
    `postings`. Through that join the planner cannot see which terms they are, guessed about 90
    postings per term (a common one has 26,000) and checked the length and any filter once per
    posting with an index lookup. With the terms in the WHERE it reads their real counts from
    the column statistics and hash-joins instead: 2 to 6 times faster for common words.
    """
    frequencies = _document_frequencies(session, lang, weights)
    if not frequencies:
        return None
    df = case(frequencies, value=Posting.term)
    qtf = case({term: weights[term] for term in frequencies}, value=Posting.term)
    where = and_(
        Posting.language == lang,
        Posting.term == any_(literal(sorted(frequencies), ARRAY(String))),
    )
    return df, qtf, where


def _among(id_column, ids: Ids):
    """`id IN (subquery)` for a filter query, else `id = ANY(array)`: one parameter however
    many ids, not one placeholder per id."""
    if isinstance(ids, Select):
        return id_column.in_(ids)
    return id_column == any_(literal(list(ids), ARRAY(Integer)))


def _ordered(stmt, score, id_column, restrict: Ids | None, limit: int | None):
    """Filter to `restrict`, order best first, and cut to `limit` in the database.

    The search page needs 15 rows; a common word matches 30,000 hadiths, so the limit saves the
    sort of every match, the transfer and a dict that size in Python.
    """
    if restrict is not None:
        stmt = stmt.where(_among(id_column, restrict))
    # Scores equal to 9 decimals tie, and ties go to the lower id, so the order never depends on
    # the order the database happened to add floats in.
    stmt = stmt.order_by(func.round(cast(score, Numeric), 9).desc(), id_column)
    if limit is not None:
        stmt = stmt.limit(limit)
    return stmt


def _ranked(stmt, score, id_column, restrict: Ids | None, limit: int | None):
    return _ordered(stmt.group_by(id_column), score, id_column, restrict, limit)


def _scores(session: Session, stmt) -> Scores:
    return {int(hadith_id): float(score) for hadith_id, score in session.execute(stmt)}


def _count_terms(terms: list[str]) -> dict[str, float]:
    return {term: float(count) for term, count in Counter(terms).items()}


def term_overlap(
    session: Session,
    query: str,
    lang: str,
    restrict: Ids | None = None,
    limit: int | None = None,
) -> dict[int, int]:
    """Hadiths ranked by how many distinct query terms they contain."""
    terms = set(preprocess_query(query, lang).split())
    if not terms:
        return {}
    score = func.count(Posting.term.distinct())
    stmt = select(Posting.hadith_id, score).where(
        Posting.language == lang, Posting.term.in_(sorted(terms))
    )
    stmt = _ranked(stmt, score, Posting.hadith_id, restrict, limit)
    return {int(hadith_id): int(count) for hadith_id, count in session.execute(stmt)}


def tf_idf(
    session: Session,
    query: str,
    lang: str,
    restrict: Ids | None = None,
    limit: int | None = None,
) -> Scores:
    weights = _count_terms(preprocess_query(query, lang).split())
    if not weights:
        return {}
    terms = _query_terms(session, lang, weights)
    if terms is None:
        return {}
    n_docs, _ = _corpus_stats(session, lang)
    df, qtf, where = terms
    score = func.sum(_tfidf_weight(n_docs, df) * qtf)
    stmt = select(Posting.hadith_id, score).where(where)
    return _scores(session, _ranked(stmt, score, Posting.hadith_id, restrict, limit))


def _tfidf_weight(n_docs: int, df):
    """TF-IDF of one posting: (1 + ln tf) * ln(N / df)."""
    return (1 + func.ln(cast(Posting.tf, Float))) * func.ln(float(n_docs) / df)


def _bm25_weight(lang: str, n_docs: int, average: float, df):
    """Okapi BM25 of one posting (needs `hadith_lengths` joined for the document length)."""
    idf = func.ln((float(n_docs) - df + 0.5) / (df + 0.5))
    saturation = (K1 * ((1 - B) + B * _length_column(lang) / average)) + Posting.tf
    return (((K1 + 1) * Posting.tf) / saturation) * idf


def bm25(
    session: Session,
    query: str,
    lang: str,
    restrict: Ids | None = None,
    limit: int | None = None,
    weights: dict[str, float] | None = None,
) -> Scores:
    """Okapi BM25. `weights` ({term: weight}) replaces the query term counts (used by PRF)."""
    if weights is None:
        weights = _count_terms(preprocess_query(query, lang).split())
    if not weights:
        return {}
    n_docs, average = _corpus_stats(session, lang)
    terms = _query_terms(session, lang, weights) if n_docs else None
    if terms is None:
        return {}
    df, qtf, where = terms
    score = func.sum(_bm25_weight(lang, n_docs, average, df) * qtf)
    stmt = (
        select(Posting.hadith_id, score)
        .join(HadithLength, HadithLength.hadith_id == Posting.hadith_id)
        .where(where)
    )
    return _scores(session, _ranked(stmt, score, Posting.hadith_id, restrict, limit))


def _document_frequencies(session: Session, lang: str, terms: Collection[str]) -> dict[str, int]:
    rows = session.execute(
        select(Term.term, Term.df).where(Term.language == lang, Term.term.in_(list(terms)))
    )
    return dict(rows.all())


def _matn_column(lang: str):
    pre = HadithPreprocessed
    return pre.Preprocessed_Arabic_Matn if lang == "AR" else pre.Preprocessed_English_Matn


def _expansion_weights(
    session: Session, query: str, lang: str, top_ids: list[int]
) -> dict[str, float]:
    """Rocchio-style pseudo relevance feedback: original terms plus the best new terms."""
    n_docs, _ = _corpus_stats(session, lang)
    rows = session.execute(
        select(HadithPreprocessed.hadith_id, _matn_column(lang)).where(
            HadithPreprocessed.hadith_id.in_(top_ids)
        )
    )
    by_id = dict(rows.all())
    top_texts = [by_id[hadith_id] or "" for hadith_id in top_ids if hadith_id in by_id]
    weights = {term: PRF_ALPHA for term in preprocess_query(query, lang).split()}
    local_tf = Counter(" ".join(top_texts).split())
    frequencies = _document_frequencies(session, lang, local_tf)
    pool = {}
    for term, tf in local_tf.items():
        df = frequencies.get(term)
        if df is None or term in weights:
            continue
        pool[term] = (tf / len(top_texts)) * log((n_docs - df + 0.5) / (df + 0.5))
    best = sorted(pool.items(), key=lambda item: item[1], reverse=True)[:PRF_TERMS]
    top_score = max(pool.values()) if pool else 1
    weights.update({term: score / top_score * PRF_BETA for term, score in best})
    return weights


def bm25_prf(
    session: Session,
    query: str,
    lang: str,
    restrict: Ids | None = None,
    limit: int | None = None,
) -> Scores:
    """BM25 again with the query expanded from the top documents of a first BM25 pass.

    The feedback documents come from the whole corpus; `restrict` and `limit` apply to the
    second pass only, so a filter changes which results are shown, not the expanded query.
    """
    first_pass = bm25(session, query, lang, limit=PRF_DOCS)
    weights = _expansion_weights(session, query, lang, list(first_pass))
    return bm25(session, query, lang, restrict=restrict, limit=limit, weights=weights)


def hybrid_prf(session: Session, query: str, lang: str) -> Scores:
    """Like `bm25_prf`, but the feedback documents come from the BM25 + TF-IDF hybrid."""
    first_pass = list(bm25_tfidf_hybrid(session, query, lang, limit=PRF_DOCS))
    weights = _expansion_weights(session, query, lang, first_pass)
    return bm25(session, query, lang, weights=weights)


def bm25_tfidf_hybrid(
    session: Session,
    query: str,
    lang: str,
    restrict: Ids | None = None,
    limit: int | None = None,
) -> Scores:
    """HYBRID_ALPHA x BM25 + (1 - HYBRID_ALPHA) x TF-IDF, each divided by its best score.

    One statement: both sums per hadith, then the two maxima as window functions over every
    match, and only then the filter and the limit, so a filter never changes the normalisation.
    A hadith with no length row has no BM25 score and counts 0 for it (as BM25 alone leaves it out).
    """
    weights = _count_terms(preprocess_query(query, lang).split())
    if not weights:
        return {}
    n_docs, average = _corpus_stats(session, lang)
    terms = _query_terms(session, lang, weights) if n_docs else None
    if terms is None:
        return {}
    df, qtf, where = terms
    per_hadith = (
        select(
            Posting.hadith_id.label("hadith_id"),
            func.sum(_bm25_weight(lang, n_docs, average, df) * qtf).label("bm25"),
            func.sum(_tfidf_weight(n_docs, df) * qtf).label("tfidf"),
        )
        .outerjoin(HadithLength, HadithLength.hadith_id == Posting.hadith_id)
        .where(where)
        .group_by(Posting.hadith_id)
        .subquery()
    )
    best = select(
        per_hadith,
        func.coalesce(func.max(per_hadith.c.bm25).over(), 1).label("bm25_max"),
        func.max(per_hadith.c.tfidf).over().label("tfidf_max"),
    ).subquery()
    score = HYBRID_ALPHA * (func.coalesce(best.c.bm25, 0) / best.c.bm25_max) + (
        1 - HYBRID_ALPHA
    ) * (best.c.tfidf / best.c.tfidf_max)
    stmt = _ordered(select(best.c.hadith_id, score), score, best.c.hadith_id, restrict, limit)
    return _scores(session, stmt)


def _embedding_table():
    """The table dense search reads: `hadith_embeddings`, or the release EMBEDDINGS_RELEASE names."""
    release = get_settings().embeddings_release
    return HadithEmbedding.__table__ if release is None else embedding_table(release)


def _embedding_column(lang: str):
    if lang != "AR":
        raise ValueError("Dense search supports Arabic only (the sentence encoder is Arabic)")
    return _embedding_table().c.arabic


def dense_search(
    session: Session,
    query_embedding,
    lang: str,
    top_k: int,
    restrict: Ids | None = None,
) -> Scores:
    """Exact cosine search (no vector index): score is 1 - cosine distance, best first."""
    vector = _embedding_column(lang)
    distance = vector.cosine_distance([float(x) for x in query_embedding])
    hadith_id = vector.table.c.hadith_id
    stmt = select(hadith_id, 1 - distance).where(vector.is_not(None))
    if restrict is not None:
        stmt = stmt.where(_among(hadith_id, restrict))
    stmt = stmt.order_by(distance, hadith_id).limit(top_k)
    return _scores(session, stmt)


QUERY_CACHE_SIZE = 1024


@lru_cache(maxsize=QUERY_CACHE_SIZE)
def _encoded(model, text: str, release: str | None):
    with timing.time_encode():
        vector = model.encode([text])[0]
    vector = np.array(vector, dtype=float)
    vector.setflags(write=False)  # shared between requests
    return vector


def encode_query(model, query: str, lang: str):
    """Unit vector for an Arabic query; the same cleanup the hadiths got when they were encoded.

    Repeated queries are served from a cache keyed by the encoder object and the embeddings
    release, so switching the model or the release never returns an old vector.
    """
    if lang != "AR":
        raise ValueError("Dense search supports Arabic only (the sentence encoder is Arabic)")
    return _encoded(model, encoding_text(query), get_settings().embeddings_release)


def _ranks(scores: Scores) -> dict[int, int]:
    return {hadith_id: rank for rank, hadith_id in enumerate(scores, 1)}


def cosine_search(
    session: Session,
    query: str,
    lang: str,
    model,
    top_k: int,
    restrict: Ids | None = None,
) -> Scores:
    return dense_search(session, encode_query(model, query, lang), lang, top_k, restrict=restrict)


def semantic_rerank(
    session: Session, query: str, lang: str, candidate_ids: list[int], model, top_k: int
) -> Scores:
    """Order BM25 candidates by dense similarity, keeping the best `top_k`."""
    return dense_search(
        session, encode_query(model, query, lang), lang, top_k, restrict=list(candidate_ids)
    )


def _fused_candidates(
    session: Session,
    query: str,
    lang: str,
    model,
    candidate_k: int,
    restrict: Ids | None,
) -> Scores:
    lexical = bm25(session, query, lang, restrict=restrict, limit=candidate_k)
    dense = dense_search(session, encode_query(model, query, lang), lang, candidate_k, restrict)
    return rrf_fusion([_ranks(lexical), _ranks(dense)])


def bm25_dense_rrf(
    session: Session,
    query: str,
    lang: str,
    model,
    candidate_k: int = 500,
    top_k: int = 50,
    restrict: Ids | None = None,
) -> Scores:
    """Reciprocal rank fusion of the BM25 and dense rankings."""
    fused = _fused_candidates(session, query, lang, model, candidate_k, restrict)
    return dict(list(fused.items())[:top_k])


def cross_rerank(
    session: Session, query: str, candidate_ids: list[int], rerank, top_k: int
) -> Scores:
    """Order candidates by a cross-encoder reading the question and the Arabic matn together.

    `rerank(query, texts)` returns one score per text. A candidate without Arabic matn cannot be
    scored and is left out.
    """
    rows = session.execute(
        select(Hadith.id, Hadith.Arabic_Matn).where(Hadith.id.in_(candidate_ids))
    ).all()
    texts = {hadith_id: matn for hadith_id, matn in rows if matn}
    ids = [i for i in candidate_ids if i in texts]
    if not ids:
        return {}
    scores = rerank(query, [texts[i] for i in ids])
    ranked = sorted(zip(ids, scores), key=lambda pair: pair[1], reverse=True)
    return dict(ranked[:top_k])
