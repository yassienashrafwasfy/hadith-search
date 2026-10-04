"""Ranking and embedding-store checks that the parity tests cannot see (mutmut survivors)."""

import numpy as np
import pytest
from sqlalchemy import insert, select

import database
from models import HadithEmbedding, HadithLength, Posting, Term
from scripts import embedding_store
from services import ranking


@pytest.fixture
def _shared_term(_patched_paths, monkeypatch):
    """Term 'zz' is in hadith 1 for English and in hadiths 2 and 3 for Arabic (df 1, so idf > 0)."""
    # only the Arabic cleanup lower-cases, so a query run in the wrong language finds nothing
    monkeypatch.setattr(ranking, "preprocess_english", lambda text: text)
    monkeypatch.setattr(ranking, "preprocess_arabic", lambda text: text.lower())
    with database.get_sync_session() as session:
        session.execute(
            insert(Term),
            [
                {"language": "EN", "term": "zz", "df": 1},
                {"language": "AR", "term": "zz", "df": 1},
                {"language": "EN", "term": "yy", "df": 7},
                {"language": "AR", "term": "yy", "df": 2},
            ],
        )
        session.execute(
            insert(Posting),
            [
                {"language": "EN", "term": "zz", "hadith_id": 1, "tf": 2},
                {"language": "AR", "term": "zz", "hadith_id": 2, "tf": 1},
                {"language": "AR", "term": "zz", "hadith_id": 3, "tf": 3},
            ],
        )
        session.execute(
            insert(HadithLength),
            [{"hadith_id": i, "english_len": 4, "arabic_len": 6} for i in (1, 2, 3)],
        )
        session.commit()
        yield session


def test_preprocessing_follows_the_language(monkeypatch):
    monkeypatch.setattr(ranking, "preprocess_arabic", lambda text: "ar:" + text)
    monkeypatch.setattr(ranking, "preprocess_english", lambda text: "en:" + text)
    assert ranking.preprocess_query("q", "AR") == "ar:q"
    assert ranking.preprocess_query("q", "EN") == "en:q"
    assert ranking.preprocess_query("q", "ar") == "en:q"


def test_every_ranking_stays_inside_its_language(_shared_term):
    session = _shared_term
    assert ranking.term_overlap(session, "zz", "EN") == {1: 1}
    assert ranking.term_overlap(session, "ZZ", "AR") == {2: 1, 3: 1}
    assert list(ranking.tf_idf(session, "zz", "EN")) == [1]
    assert list(ranking.tf_idf(session, "ZZ", "AR")) == [3, 2]
    assert list(ranking.bm25(session, "zz", "EN")) == [1]
    assert list(ranking.bm25(session, "ZZ", "AR")) == [3, 2]
    assert ranking._document_frequencies(session, "EN", ["zz", "yy"]) == {"zz": 1, "yy": 7}
    assert ranking._document_frequencies(session, "AR", ["zz", "yy"]) == {"zz": 1, "yy": 2}
    assert ranking._document_frequencies(session, "AR", []) == {}


def test_restrict_and_limit_apply_to_every_lexical_ranking(_shared_term):
    session = _shared_term
    assert ranking.term_overlap(session, "ZZ", "AR", restrict={3}) == {3: 1}
    assert list(ranking.tf_idf(session, "ZZ", "AR", restrict={2})) == [2]
    assert list(ranking.tf_idf(session, "ZZ", "AR", limit=1)) == [3]
    assert list(ranking.bm25(session, "ZZ", "AR", restrict={2})) == [2]
    assert list(ranking.bm25(session, "ZZ", "AR", limit=1)) == [3]
    assert ranking.term_overlap(session, "ZZ", "AR", restrict=set()) == {}


def test_corpus_stats_count_documents_and_average_the_language_length(_shared_term):
    assert ranking._corpus_stats(_shared_term, "EN") == (3, 4.0)
    assert ranking._corpus_stats(_shared_term, "AR") == (3, 6.0)


def test_hybrid_scores_are_a_weighted_sum_of_the_two_normalised_scores(_shared_term):
    session = _shared_term
    bm25 = ranking.bm25(session, "ZZ", "AR")
    tfidf = ranking.tf_idf(session, "ZZ", "AR")
    hybrid = ranking.bm25_tfidf_hybrid(session, "ZZ", "AR")
    assert list(hybrid) == [3, 2]
    for hadith_id in (2, 3):
        expected = 0.8 * bm25[hadith_id] / max(bm25.values()) + 0.2 * (
            tfidf[hadith_id] / max(tfidf.values())
        )
        assert hybrid[hadith_id] == pytest.approx(expected)
    assert max(hybrid.values()) == pytest.approx(1.0)
    assert ranking.bm25_tfidf_hybrid(session, "NOMATCH", "AR") == {}


def test_expansion_weights_start_from_the_query_terms(_shared_term):
    weights = ranking._expansion_weights(_shared_term, "ZZ", "AR", [])
    assert weights == {"zz": 1.0}


def test_dense_ties_go_to_the_lower_id_and_top_k_is_honoured(_patched_paths):
    same = [0.5, 0.25, 0.125]
    with database.get_sync_session() as session:
        session.execute(
            insert(HadithEmbedding),
            [{"hadith_id": i, "arabic": same} for i in (3, 1, 2)],
        )
        session.commit()
        assert list(ranking.dense_search(session, same, "AR", 3)) == [1, 2, 3]
        assert list(ranking.dense_search(session, same, "AR", 2)) == [1, 2]
        model = type("M", (), {"encode": lambda self, texts: np.array([same] * len(texts))})()
        assert list(ranking.cosine_search(session, "q", "AR", model, 2)) == [1, 2]
        assert list(ranking.semantic_rerank(session, "q", "AR", [3, 2], model, 1)) == [2]


def test_the_arabic_only_message_is_exact():
    message = "Dense search supports Arabic only (the sentence encoder is Arabic)"
    with pytest.raises(ValueError) as error:
        ranking._embedding_column("EN")
    assert str(error.value) == message
    with pytest.raises(ValueError) as error:
        ranking.encode_query(None, "q", "EN")
    assert str(error.value) == message


def test_store_embeddings_upserts_in_batches_and_checks_lengths(_patched_paths, monkeypatch):
    monkeypatch.setattr(embedding_store, "BATCH", 2)
    with database.get_sync_session() as session:
        stored = embedding_store.store_embeddings(
            session, [1, 2, 3], [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        )
        assert stored == 3
        again = embedding_store.store_embeddings(session, [2], [[0.5, 0.5, 0]])
        assert again == 1
        rows = dict(
            session.execute(select(HadithEmbedding.hadith_id, HadithEmbedding.arabic)).all()
        )
        assert list(rows[1]) == [1, 0, 0] and list(rows[3]) == [0, 0, 1]
        assert list(rows[2]) == [0.5, 0.5, 0]
        count = session.query(HadithEmbedding).count()
        assert count == 3
        with pytest.raises(ValueError) as error:
            embedding_store.store_embeddings(session, [1, 2], [[1, 0, 0]])
        assert str(error.value) == "1 embeddings for 2 hadiths"
