import time

import numpy as np
import pytest
from sqlalchemy import insert, inspect

import database
import timing
from models import Annotation, Annotator, Assignment, SearchRequest
from services import ranking, retrieval
from settings import get_settings

SEARCH = "/api/v1/searches"


def _entries(res) -> dict[str, str]:
    parts = [p.strip() for p in res.headers["server-timing"].split(", ")]
    return {p.split(";")[0]: p for p in parts}


async def test_server_timing_splits_database_and_encoder_time(_search_client):
    lexical = _entries(await _search_client.get(SEARCH, params={"q": "prayer", "method": "bm25"}))
    assert lexical["search"].startswith("search;dur=")
    assert lexical["db"].startswith("db;dur=") and "statements" in lexical["db"]
    assert "encode" not in lexical
    dense = _entries(
        await _search_client.get(
            SEARCH, params={"q": "صلاه", "method": "cosine-similarity", "lang": "ar"}
        )
    )
    assert dense["encode"].startswith("encode;dur=")


async def test_statements_are_counted_for_the_request(_search_client):
    res = await _search_client.get(SEARCH, params={"q": "prayer", "method": "term-overlap"})
    # the ranking query and the row fetch
    assert 'desc="2 statements"' in _entries(res)["db"]


def test_nothing_is_recorded_outside_a_request(_db_session):
    timing._current.set(None)
    ranking._corpus_stats(_db_session, "EN")  # must not raise without a record


def test_corpus_stats_are_read_once_until_cleared_or_expired(_db_session, monkeypatch):
    record = timing.start()
    first = ranking._corpus_stats(_db_session, "EN")
    again = ranking._corpus_stats(_db_session, "EN")
    assert first == again and record.statements == 1
    ranking._corpus_stats(_db_session, "AR")  # another language is its own entry
    assert record.statements == 2
    ranking.clear_corpus_stats()
    ranking._corpus_stats(_db_session, "EN")
    assert record.statements == 3
    later = time.monotonic() + ranking.STATS_TTL_SECONDS + 1
    monkeypatch.setattr(ranking.time, "monotonic", lambda: later)
    ranking._corpus_stats(_db_session, "EN")
    assert record.statements == 4


class _CountingModel:
    def __init__(self):
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        return np.array([[1.0, 0.0, 0.0] for _ in texts])


def test_repeated_queries_are_encoded_once():
    model = _CountingModel()
    first = ranking.encode_query(model, "الصَّلَاة", "AR")
    second = ranking.encode_query(model, "الصلاة", "AR")  # same text once diacritics are gone
    assert model.calls == 1 and np.array_equal(first, second)
    assert not first.flags.writeable  # shared between requests, so it cannot be changed
    ranking.encode_query(model, "الصيام", "AR")
    assert model.calls == 2
    ranking.encode_query(_CountingModel(), "الصلاة", "AR")  # another encoder: its own entry


def test_a_new_embeddings_release_does_not_reuse_old_vectors(monkeypatch):
    model = _CountingModel()
    ranking.encode_query(model, "الصلاة", "AR")
    monkeypatch.setenv("EMBEDDINGS_RELEASE", "mv2")
    get_settings.cache_clear()
    ranking.encode_query(model, "الصلاة", "AR")
    assert model.calls == 2


def test_the_hadith_id_and_query_id_columns_are_indexed(_db_session):
    inspector = inspect(database.get_sync_engine())
    names = {
        table: {i["name"] for i in inspector.get_indexes(table)}
        for table in ("annotations", "kv_pairs", "assignments")
    }
    assert "ix_annotations_hadith_id" in names["annotations"]
    assert "idx_kv_pairs_hadith_id" in names["kv_pairs"]
    assert "ix_assignments_query_id" in names["assignments"]


async def test_init_schema_adds_a_missing_index_to_an_existing_table(_patched_paths):
    from sqlalchemy import DDL

    with database.get_sync_engine().begin() as conn:
        conn.execute(DDL('DROP INDEX "ix_annotations_hadith_id"'))
    await database.init_schema()
    indexes = inspect(database.get_sync_engine()).get_indexes("annotations")
    assert "ix_annotations_hadith_id" in {i["name"] for i in indexes}


async def test_labels_are_read_in_one_query_for_all_annotators(_db_session):
    from routers.annotation import _labels_by_annotator

    for name in ("a", "b", "c"):
        _db_session.execute(
            insert(Annotator).values(
                username=name, password_hash="h", password_salt="s", created_at="t"
            )
        )
    ids = [row.id for row in _db_session.query(Annotator).order_by(Annotator.id)]
    _db_session.execute(
        insert(Assignment), [{"annotator_id": i, "query_id": "q1", "assigned_at": "t"} for i in ids]
    )
    _db_session.execute(
        insert(Annotation),
        [
            {
                "annotator_id": ids[0],
                "query_id": "q1",
                "hadith_id": 1,
                "label": 2,
                "created_at": "t",
                "updated_at": "t",
            },
            {
                "annotator_id": ids[0],
                "query_id": "q1",
                "hadith_id": 3,
                "label": 0,
                "created_at": "t",
                "updated_at": "t",
            },
            {
                "annotator_id": ids[1],
                "query_id": "q1",
                "hadith_id": 1,
                "label": 1,
                "created_at": "t",
                "updated_at": "t",
            },
            {
                "annotator_id": ids[0],
                "query_id": "q2",
                "hadith_id": 2,
                "label": 2,
                "created_at": "t",
                "updated_at": "t",
            },
        ],
    )
    _db_session.commit()
    record = timing.start()
    async with database.get_session() as session:
        labels = await _labels_by_annotator(session, "q1")
    assert labels == {ids[0]: {1: 2, 3: 0}, ids[1]: {1: 1}, ids[2]: {}}
    assert record.statements == 2  # the assignments, then every annotator's labels at once
    async with database.get_session() as session:
        assert await _labels_by_annotator(session, "no-such-query") == {}


@pytest.mark.parametrize("method", ["cosine-similarity", "semantic-rerank", "semantic-rrf"])
async def test_dense_methods_filter_before_they_cut(
    _search_index, _mock_preprocess, _fake_model, monkeypatch, method
):
    """The model prefers hadith 1 and then 3 (both Sahih). With room for one result, a grade
    filter for Hasan must still return hadith 2, not nothing."""
    monkeypatch.setattr(retrieval, "COSINE_TOP_K", 1)
    monkeypatch.setattr(retrieval, "RERANK_TOP_K", 1)
    request = SearchRequest(query="صيام", lang="ar", grade_filter="Hasan")
    with database.get_sync_session() as session:
        ctx = retrieval.SearchContext(session=session, model=lambda: _fake_model)
        response = retrieval.run_search(retrieval.SYSTEMS[method], ctx, request)
    assert [r.hadith.hadith_id for r in response.results] == [2]


def test_default_page_is_fifteen_results():
    from services import results

    assert results.DEFAULT_TOP_K == 15
