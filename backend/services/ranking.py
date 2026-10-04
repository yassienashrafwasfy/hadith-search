"""Ranking over PostgreSQL: BM25 and TF-IDF from the postings tables, dense search with pgvector.

Every function takes a sync SQLAlchemy `Session` and returns `{hadith_id: score}` ordered best
first, the same shape the old in-memory code returned. The BM25 and TF-IDF formulas are the ones
the pickled index used, now evaluated in SQL (see `_bm25_statement`). `restrict` limits a search
to a set of hadith ids; the evaluation pipeline uses it for its judged pool.
"""

from collections import Counter
from collections.abc import Collection
from math import log

from sqlalchemy import Float, Numeric, String, and_, cast, column, func, select, values
from sqlalchemy.orm import Session

from models import HadithEmbedding, HadithLength, HadithPreprocessed, Posting, Term
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


def preprocess_query(query: str, lang: str) -> str:
    return preprocess_arabic(query) if lang == "AR" else preprocess_english(query)


def _length_column(lang: str):
    return HadithLength.arabic_len if lang == "AR" else HadithLength.english_len


def _corpus_stats(session: Session, lang: str) -> tuple[int, float]:
    """(number of documents, average document length in the language)."""
    n_docs, average = session.execute(
        select(func.count(), func.avg(_length_column(lang))).select_from(HadithLength)
    ).one()
    return n_docs, float(average or 0)


def _weights_table(weights: dict[str, float]):
    return values(column("term", String), column("qtf", Float), name="q").data(
        list(weights.items())
    )


def _ranked(stmt, score, id_column, restrict: Collection[int] | None, limit: int | None):
    if restrict is not None:
        stmt = stmt.where(id_column.in_(list(restrict)))
    # Scores equal to 9 decimals tie, and ties go to the lower id, so the order never depends on
    # the order the database happened to add floats in.
    stmt = stmt.group_by(id_column).order_by(func.round(cast(score, Numeric), 9).desc(), id_column)
    if limit is not None:
        stmt = stmt.limit(limit)
    return stmt


def _scores(session: Session, stmt) -> Scores:
    return {int(hadith_id): float(score) for hadith_id, score in session.execute(stmt)}


def _count_terms(terms: list[str]) -> dict[str, float]:
    return {term: float(count) for term, count in Counter(terms).items()}


def term_overlap(
    session: Session, query: str, lang: str, restrict: Collection[int] | None = None
) -> dict[int, int]:
    """Hadiths ranked by how many distinct query terms they contain."""
    terms = set(preprocess_query(query, lang).split())
    if not terms:
        return {}
    score = func.count(Posting.term.distinct())
    stmt = select(Posting.hadith_id, score).where(
        Posting.language == lang, Posting.term.in_(sorted(terms))
    )
    stmt = _ranked(stmt, score, Posting.hadith_id, restrict, None)
    return {int(hadith_id): int(count) for hadith_id, count in session.execute(stmt)}


def tf_idf(
    session: Session,
    query: str,
    lang: str,
    restrict: Collection[int] | None = None,
    limit: int | None = None,
) -> Scores:
    weights = _count_terms(preprocess_query(query, lang).split())
    if not weights:
        return {}
    n_docs, _ = _corpus_stats(session, lang)
    q = _weights_table(weights)
    score = func.sum(
        (1 + func.ln(cast(Posting.tf, Float))) * func.ln(float(n_docs) / Term.df) * q.c.qtf
    )
    stmt = _term_postings(select(Posting.hadith_id, score), q, lang)
    return _scores(session, _ranked(stmt, score, Posting.hadith_id, restrict, limit))


def _term_postings(stmt, q, lang: str):
    """FROM the query terms, joined to their document frequency and postings."""
    return (
        stmt.select_from(q)
        .join(Term, and_(Term.language == lang, Term.term == q.c.term))
        .join(Posting, and_(Posting.language == Term.language, Posting.term == Term.term))
    )


def bm25(
    session: Session,
    query: str,
    lang: str,
    restrict: Collection[int] | None = None,
    limit: int | None = None,
    weights: dict[str, float] | None = None,
) -> Scores:
    """Okapi BM25. `weights` ({term: weight}) replaces the query term counts (used by PRF)."""
    if weights is None:
        weights = _count_terms(preprocess_query(query, lang).split())
    if not weights:
        return {}
    n_docs, average = _corpus_stats(session, lang)
    if not n_docs:
        return {}
    q = _weights_table(weights)
    length = _length_column(lang)
    idf = func.ln((float(n_docs) - Term.df + 0.5) / (Term.df + 0.5))
    saturation = (K1 * ((1 - B) + B * length / average)) + Posting.tf
    score = func.sum((((K1 + 1) * Posting.tf) / saturation) * idf * q.c.qtf)
    stmt = _term_postings(select(Posting.hadith_id, score), q, lang).join(
        HadithLength, HadithLength.hadith_id == Posting.hadith_id
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


def bm25_prf(session: Session, query: str, lang: str) -> Scores:
    first_pass = bm25(session, query, lang, limit=PRF_DOCS)
    weights = _expansion_weights(session, query, lang, list(first_pass))
    return bm25(session, query, lang, weights=weights)


def hybrid_prf(session: Session, query: str, lang: str) -> Scores:
    """Like `bm25_prf`, but the feedback documents come from the BM25 + TF-IDF hybrid."""
    first_pass = list(bm25_tfidf_hybrid(session, query, lang))[:PRF_DOCS]
    weights = _expansion_weights(session, query, lang, first_pass)
    return bm25(session, query, lang, weights=weights)


def bm25_tfidf_hybrid(session: Session, query: str, lang: str) -> Scores:
    bm25_scores = bm25(session, query, lang)
    tfidf_scores = tf_idf(session, query, lang)
    bm25_max = max(bm25_scores.values(), default=1)
    tfidf_max = max(tfidf_scores.values(), default=1)
    combined = {
        hadith_id: HYBRID_ALPHA * (bm25_scores.get(hadith_id, 0) / bm25_max)
        + (1 - HYBRID_ALPHA) * (tfidf_scores.get(hadith_id, 0) / tfidf_max)
        for hadith_id in set(bm25_scores) | set(tfidf_scores)
    }
    return dict(sorted(combined.items(), key=lambda item: (-round(item[1], 9), item[0])))


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
    restrict: Collection[int] | None = None,
) -> Scores:
    """Exact cosine search (no vector index): score is 1 - cosine distance, best first."""
    vector = _embedding_column(lang)
    distance = vector.cosine_distance([float(x) for x in query_embedding])
    hadith_id = vector.table.c.hadith_id
    stmt = select(hadith_id, 1 - distance).where(vector.is_not(None))
    if restrict is not None:
        stmt = stmt.where(hadith_id.in_(list(restrict)))
    stmt = stmt.order_by(distance, hadith_id).limit(top_k)
    return _scores(session, stmt)


def encode_query(model, query: str, lang: str):
    """Unit vector for an Arabic query; the same cleanup the hadiths got when they were encoded."""
    if lang != "AR":
        raise ValueError("Dense search supports Arabic only (the sentence encoder is Arabic)")
    return model.encode([encoding_text(query)])[0]


def _ranks(scores: Scores) -> dict[int, int]:
    return {hadith_id: rank for rank, hadith_id in enumerate(scores, 1)}


def cosine_search(session: Session, query: str, lang: str, model, top_k: int) -> Scores:
    return dense_search(session, encode_query(model, query, lang), lang, top_k)


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
    restrict: Collection[int] | None,
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
    restrict: Collection[int] | None = None,
) -> Scores:
    """Reciprocal rank fusion of the BM25 and dense rankings."""
    fused = _fused_candidates(session, query, lang, model, candidate_k, restrict)
    return dict(list(fused.items())[:top_k])
