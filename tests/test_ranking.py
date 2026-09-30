"""SQL ranking (services/ranking.py) against the frozen in-memory code it replaced.

The oracle in `_legacy_search.py` runs on an inverted index read back out of the same tables,
so a difference is a difference in the formulas, not in the data.
"""

import random

import _legacy_search as legacy
import numpy as np
import pytest
from sqlalchemy import insert

import database
from models import Hadith, HadithEmbedding, HadithLength, Posting
from scripts.build_inverted_index import write_index
from services import ranking

VOCAB = "prayer fast zakat hajj faith mercy charity patience truth honesty neighbour water".split()
AR_VOCAB = "صلاه صيام زكاه حج ايمان رحمه صدقه صبر صدق امانه جار ماء".split()
DIM = 8


def _corpus(seed=7, size=60):
    rng = random.Random(seed)
    docs = []
    for hadith_id in range(1, size + 1):
        english = " ".join(rng.choices(VOCAB, k=rng.randint(3, 14)))
        arabic = " ".join(rng.choices(AR_VOCAB, k=rng.randint(3, 14)))
        docs.append(
            {
                "id": hadith_id,
                "Book": "Bukhari" if hadith_id % 2 else "Muslim",
                "Preprocessed_English_Matn": english,
                "Preprocessed_Arabic_Matn": arabic,
            }
        )
    return docs


@pytest.fixture
def _session(_pg_schema):
    database.init_schema_sync()
    docs = _corpus()
    with database.get_sync_session() as session:
        session.execute(insert(Hadith), docs)
        session.commit()
        write_index(session, database.read_hadiths_df())
        rng = np.random.default_rng(3)
        vectors = rng.normal(size=(len(docs), DIM)).astype(np.float32)
        session.execute(
            insert(HadithEmbedding),
            [
                {"hadith_id": d["id"], "english": v.tolist(), "arabic": (-v).tolist()}
                for d, v in zip(docs, vectors)
            ],
        )
        session.commit()
        yield session


@pytest.fixture
def _oracle(_session):
    """Legacy-shaped inverted index and document lengths read back from the tables."""
    index = {"EN": {}, "AR": {}}
    for language, term, hadith_id, tf in _session.execute(
        Posting.__table__.select().order_by(Posting.hadith_id)
    ):
        index[language].setdefault(term, []).append((hadith_id, tf))
    lengths = {
        hid: (ar, en)
        for hid, en, ar in _session.execute(
            HadithLength.__table__.select().with_only_columns(
                HadithLength.hadith_id, HadithLength.english_len, HadithLength.arabic_len
            )
        )
    }
    return index, lengths


def _matn(hadith_id, lang):
    column = "Preprocessed_Arabic_Matn" if lang == "AR" else "Preprocessed_English_Matn"
    return _corpus()[hadith_id - 1][column]


def _same_ranking(actual, expected):
    assert set(actual) == set(expected)
    for hadith_id, score in expected.items():
        assert actual[hadith_id] == pytest.approx(score, rel=1e-9, abs=1e-12)
    by_score = lambda scores: sorted(scores, key=lambda h: (-round(scores[h], 9), h))  # noqa: E731
    assert list(actual) == by_score(expected)


QUERIES = [
    ("prayer", "EN"),
    ("prayer fast zakat", "EN"),
    ("mercy mercy water nothingmatches", "EN"),
    ("neighbour truth honesty hajj charity", "EN"),
    ("صلاه صيام", "AR"),
    ("جار ماء ماء صبر", "AR"),
]


@pytest.mark.usefixtures("_mock_preprocess")
class TestLexicalParity:
    @pytest.mark.parametrize(("query", "lang"), QUERIES)
    def test_bm25(self, _session, _oracle, query, lang):
        index, lengths = _oracle
        expected = legacy.bm25(query, lang, index[lang], lengths)
        _same_ranking(ranking.bm25(_session, query, lang), expected)

    @pytest.mark.parametrize(("query", "lang"), QUERIES)
    def test_tf_idf(self, _session, _oracle, query, lang):
        index, lengths = _oracle
        expected = legacy.tf_idf(query, lang, index[lang], lengths)
        _same_ranking(ranking.tf_idf(_session, query, lang), expected)

    @pytest.mark.parametrize(("query", "lang"), QUERIES)
    def test_term_overlap(self, _session, _oracle, query, lang):
        index, _ = _oracle
        expected = legacy.ranked_term_overlap(query, lang, index[lang])
        assert ranking.term_overlap(_session, query, lang) == expected

    @pytest.mark.parametrize(("query", "lang"), QUERIES)
    def test_hybrid(self, _session, _oracle, query, lang):
        index, lengths = _oracle
        expected = legacy.bm25_tfidf_hybrid(query, lang, index[lang], lengths)
        _same_ranking(ranking.bm25_tfidf_hybrid(_session, query, lang), expected)

    @pytest.mark.parametrize(("query", "lang"), QUERIES)
    def test_prf(self, _session, _oracle, query, lang, monkeypatch):
        index, lengths = _oracle
        first_pass = legacy.bm25

        def tie_broken_by_id(*args, **kwargs):  # the old code left ties in insertion order
            scores = first_pass(*args, **kwargs)
            return dict(sorted(scores.items(), key=lambda item: (-round(item[1], 9), item[0])))

        monkeypatch.setattr(legacy, "bm25", tie_broken_by_id)
        fetch = lambda hid: {  # noqa: E731
            "Preprocessed_English_Matn": _matn(hid, "EN"),
            "Preprocessed_Arabic_Matn": _matn(hid, "AR"),
        }
        expected = legacy.bm25_with_expansion(query, lang, index[lang], lengths, fetch)
        _same_ranking(ranking.bm25_prf(_session, query, lang), expected)

    @pytest.mark.parametrize(("query", "lang"), QUERIES)
    def test_hybrid_prf(self, _session, _oracle, query, lang, monkeypatch):
        index, lengths = _oracle
        first_pass = legacy.bm25

        def tie_broken_by_id(*args, **kwargs):
            scores = first_pass(*args, **kwargs)
            return dict(sorted(scores.items(), key=lambda item: (-round(item[1], 9), item[0])))

        monkeypatch.setattr(legacy, "bm25", tie_broken_by_id)
        fetch = lambda hid: {  # noqa: E731
            "Preprocessed_English_Matn": _matn(hid, "EN"),
            "Preprocessed_Arabic_Matn": _matn(hid, "AR"),
        }
        expected = legacy.hybrid_with_expansion(query, lang, index[lang], lengths, fetch)
        _same_ranking(ranking.hybrid_prf(_session, query, lang), expected)

    def test_limit_and_restrict(self, _session):
        full = ranking.bm25(_session, "prayer fast", "EN")
        assert list(ranking.bm25(_session, "prayer fast", "EN", limit=3)) == list(full)[:3]
        only = set(list(full)[:10:2])
        assert set(ranking.bm25(_session, "prayer fast", "EN", restrict=only)) == only

    def test_empty_and_unknown_queries(self, _session):
        assert ranking.bm25(_session, "", "EN") == {}
        assert ranking.bm25(_session, "zzzunknown", "EN") == {}
        assert ranking.tf_idf(_session, "zzzunknown", "EN") == {}
        assert ranking.term_overlap(_session, "", "EN") == {}


class _Model:
    def __init__(self, vector):
        self.vector = np.asarray(vector, dtype=np.float32)

    def encode(self, texts):
        return np.array([self.vector for _ in texts])


@pytest.fixture
def _vectors(_session):
    rows = _session.execute(
        HadithEmbedding.__table__.select().order_by(HadithEmbedding.hadith_id)
    ).all()
    ids = np.array([r.hadith_id for r in rows])
    matrix = np.array([r.arabic for r in rows], dtype=np.float32)
    return ids, matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


@pytest.mark.usefixtures("_mock_preprocess")
@pytest.mark.usefixtures("_mock_preprocess")
class TestDenseParity:
    QUERY = np.linspace(-1, 1, DIM)

    def test_cosine_matches_numpy(self, _session, _vectors):
        ids, matrix = _vectors
        expected = legacy.cosine_similarity_search(self.QUERY, matrix, ids, top_k=10)
        actual = ranking.dense_search(_session, self.QUERY, "AR", top_k=10)
        assert list(actual) == list(expected)
        for hadith_id, score in expected.items():
            assert actual[hadith_id] == pytest.approx(score, abs=1e-5)

    def test_english_is_rejected(self, _session):
        with pytest.raises(ValueError, match="Arabic only"):
            ranking.dense_search(_session, self.QUERY, "EN", top_k=5)
        with pytest.raises(ValueError, match="Arabic only"):
            ranking.encode_query(_Model(self.QUERY), "prayer", "EN")

    def test_rerank_only_scores_candidates(self, _session, _vectors):
        ids, matrix = _vectors
        candidates = [5, 9, 12, 20]
        expected = legacy.semantic_reranker(
            "q", "AR", candidates, _Model(self.QUERY), matrix, ids, top_k=3
        )
        actual = ranking.semantic_rerank(
            _session, "q", "AR", candidates, _Model(self.QUERY), top_k=3
        )
        assert list(actual) == list(expected)

    def test_rrf_matches_legacy(self, _session, _oracle, _vectors):
        index, lengths = _oracle
        ids, matrix = _vectors
        model = _Model(self.QUERY)
        expected = legacy.bm25_semantic_rrf(
            "صلاه صيام", "AR", index["AR"], lengths, matrix, ids, model, candidate_k=20, top_k=15
        )
        actual = ranking.bm25_dense_rrf(
            _session, "صلاه صيام", "AR", model, candidate_k=20, top_k=15
        )
        assert list(actual) == list(expected)
        for hadith_id, score in expected.items():
            assert actual[hadith_id] == pytest.approx(score)

    def test_restrict_limits_both_rankings(self, _session):
        pool = {2, 4, 6, 8, 10}
        fused = ranking.bm25_dense_rrf(
            _session, "صلاه", "AR", _Model(self.QUERY), restrict=pool, top_k=50
        )
        assert set(fused) <= pool


def test_index_rows_cover_every_hadith(_session):
    assert _session.query(HadithLength).count() == 60
    assert _session.query(Posting).count() > 60
