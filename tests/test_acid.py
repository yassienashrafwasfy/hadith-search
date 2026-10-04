"""ACID guarantees of the write path: rollback on failure, constraints, and racing sessions."""

import asyncio

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from test_database import _corpus_frame

import database
from models import (
    Annotation,
    AnnotationProgress,
    Annotator,
    Assignment,
    Hadith,
    HadithEmbedding,
    KvPair,
    Posting,
)

API = "/api/v1"


def _count(model) -> int:
    with database.get_sync_session() as session:
        return session.scalar(select(func.count()).select_from(model))


def _annotator_row(name="a"):
    return {"username": name, "password_hash": "h", "password_salt": "s", "created_at": "t"}


def _signup(client, name):
    return client.post(f"{API}/annotators", json={"username": name, "password": "secret123"})


# ---------- Atomicity ----------


def test_index_rewrite_failure_keeps_the_old_index(_search_index, monkeypatch):
    from scripts import build_inverted_index as bii

    before = (_count(Posting), _count(bii.Term), _count(bii.HadithLength))
    real = bii._insert_in_batches

    def fail_on_postings(session, model, rows):
        if model is Posting:
            raise RuntimeError("disk full")
        real(session, model, rows)

    monkeypatch.setattr(bii, "_insert_in_batches", fail_on_postings)
    with pytest.raises(RuntimeError):
        with database.get_sync_session() as session:
            bii.write_index(session, database.read_hadiths_df())
    assert (_count(Posting), _count(bii.Term), _count(bii.HadithLength)) == before
    assert before[0] > 0


def test_embeddings_upsert_is_all_or_nothing(_patched_paths, monkeypatch):
    from scripts import embedding_store

    monkeypatch.setattr(embedding_store, "BATCH", 1)
    # id 999 has no hadith, so the second batch breaks the foreign key after the first succeeded
    with pytest.raises(IntegrityError):
        with database.get_sync_session() as session:
            embedding_store.store_embeddings(session, [1, 999], [[0.1, 0.2], [0.3, 0.4]])
    assert _count(HadithEmbedding) == 0


async def test_signup_failure_after_insert_leaves_nothing(_client, monkeypatch):
    from routers import auth

    async def boom(annotator_id, session):
        raise RuntimeError("assignment failed")

    monkeypatch.setattr(auth, "auto_assign_queries", boom)
    with pytest.raises(RuntimeError):
        await _signup(_client, "alice")
    assert _count(Annotator) == 0 and _count(Assignment) == 0


def test_corpus_rebuild_failure_restores_the_old_corpus(_patched_paths):
    from scripts import data_creation

    frame = _corpus_frame([7, 7])  # duplicate primary key: the insert fails after the drop
    with pytest.raises(IntegrityError):
        data_creation.create_database(frame)
    assert sorted(database.read_hadiths_df()["id"]) == [1, 2, 3]


def test_migration_failure_copies_nothing(_pg_schema, tmp_path, monkeypatch):
    from test_migrate_to_postgres import _old_install

    from scripts import migrate_to_postgres as mig

    def index_fails(session, df, commit=True):
        raise RuntimeError("index build failed")

    monkeypatch.setattr(mig, "write_index", index_fails)
    with pytest.raises(RuntimeError):
        mig.migrate(str(_old_install(tmp_path)), str(tmp_path))
    assert _count(Hadith) == 0 and _count(Annotator) == 0


# ---------- Consistency ----------


def _violates(statement):
    with pytest.raises(IntegrityError):
        with database.get_sync_session() as session:
            session.execute(statement)
            session.commit()


def test_username_is_unique(_patched_paths):
    with database.get_sync_session() as session:
        session.execute(insert(Annotator), _annotator_row())
        session.commit()
    _violates(insert(Annotator).values(**_annotator_row()))


def test_foreign_keys_are_enforced(_patched_paths):
    _violates(insert(Assignment).values(annotator_id=99, query_id="q1", assigned_at="t"))
    _violates(insert(HadithEmbedding).values(hadith_id=999, arabic=[0.1]))


def test_one_assignment_per_annotator_and_query(_patched_paths):
    with database.get_sync_session() as session:
        session.execute(insert(Annotator), _annotator_row())
        session.execute(insert(Assignment).values(annotator_id=1, query_id="q1", assigned_at="t"))
        session.commit()
    _violates(insert(Assignment).values(annotator_id=1, query_id="q1", assigned_at="t"))


def test_invalid_values_are_rejected(_patched_paths):
    with database.get_sync_session() as session:
        session.execute(insert(Annotator), _annotator_row())
        session.commit()
    row = {"annotator_id": 1, "query_id": "q1", "hadith_id": 1, "created_at": "t"}
    _violates(insert(Annotation).values(**row, label=3, updated_at="t"))
    _violates(insert(AnnotationProgress).values(annotator_id=1, query_id="q1", current_index=-1))
    _violates(
        insert(KvPair).values(
            topic="t",
            language="en",
            concept_en="c",
            concept_ar="c",
            entity_en="e",
            entity_ar="e",
            hadith_id=1,
            status="bogus",
            created_at="t",
        )
    )


def test_deleting_an_annotator_cascades(_patched_paths):
    with database.get_sync_session() as session:
        session.execute(insert(Annotator), _annotator_row())
        session.execute(insert(Assignment).values(annotator_id=1, query_id="q1", assigned_at="t"))
        session.execute(
            insert(Annotation).values(
                annotator_id=1, query_id="q1", hadith_id=1, label=1, created_at="t", updated_at="t"
            )
        )
        session.delete(session.get(Annotator, 1))
        session.commit()
    assert _count(Assignment) == 0 and _count(Annotation) == 0


# ---------- Isolation ----------


async def test_same_username_race_gives_one_account_and_one_409(_client):
    first, second = await asyncio.gather(_signup(_client, "bob"), _signup(_client, "bob"))
    assert sorted([first.status_code, second.status_code]) == [201, 409]
    assert _count(Annotator) == 1


async def test_concurrent_signups_never_exceed_annotators_per_query(_client):
    from routers.auth import ANNOTATORS_PER_QUERY

    names = [f"user{i}" for i in range(6)]
    responses = await asyncio.gather(*(_signup(_client, n) for n in names))
    assert {r.status_code for r in responses} == {201}
    with database.get_sync_session() as session:
        per_query = session.execute(
            select(func.count()).select_from(Assignment).group_by(Assignment.query_id)
        ).scalars()
        assert max(per_query) <= ANNOTATORS_PER_QUERY


async def test_concurrent_first_labels_report_one_creation(_client, _auth_headers):
    url = f"{API}/assignments/q1/labels/1"
    first, second = await asyncio.gather(
        _client.put(url, json={"label": 1}, headers=_auth_headers),
        _client.put(url, json={"label": 2}, headers=_auth_headers),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 201]
    assert _count(Annotation) == 1


async def test_overlapping_kv_batches_do_not_deadlock(_client, _auth_headers, _kv_rows):
    forward = [{"id": 1, "status": "verified"}, {"id": 2, "status": "verified"}]
    results = await asyncio.gather(
        *(
            _client.patch(
                f"{API}/kv-pairs", json=forward[:: 1 if i % 2 else -1], headers=_auth_headers
            )
            for i in range(8)
        )
    )
    assert {r.status_code for r in results} == {200}


async def test_schema_initialisers_can_run_together(_pg_schema):
    await asyncio.gather(*(database.init_schema() for _ in range(4)))
    assert _count(Annotator) == 0
