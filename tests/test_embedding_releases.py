"""Per-release embedding tables: EMBEDDINGS_RELEASE, the startup check, search and cleanup."""

import numpy as np
import pytest
from sqlalchemy import func, inspect, select

import database
import startup
from models import EmbeddingSet, HadithEmbedding
from models.embedding_sets import embedding_table, table_name
from scripts.embedding_store import store_embeddings
from services import ranking
from settings import Settings, get_settings


def test_release_setting_is_optional_empty_means_default_and_is_validated(monkeypatch):
    monkeypatch.delenv("EMBEDDINGS_RELEASE", raising=False)
    assert Settings().embeddings_release is None
    monkeypatch.setenv("EMBEDDINGS_RELEASE", "")
    assert Settings().embeddings_release is None
    monkeypatch.setenv("EMBEDDINGS_RELEASE", "mv7")
    assert Settings().embeddings_release == "mv7"
    for bad in ("MV7", "7mv", "a-b", "x; drop table hadiths", "a" * 41):
        monkeypatch.setenv("EMBEDDINGS_RELEASE", bad)
        with pytest.raises(ValueError, match="Embeddings release"):
            Settings()


def test_table_name_has_the_prefix():
    assert table_name("mv7") == "hadith_embeddings_mv7"
    assert embedding_table("mv7") is embedding_table("mv7")


def _make_release(release: str, ids=(1, 2, 3), vectors=None):
    vectors = np.eye(3)[:3] if vectors is None else vectors
    with database.get_sync_engine().begin() as conn:
        embedding_table(release).create(conn, checkfirst=True)
    with database.get_sync_session() as session:
        store_embeddings(session, list(ids), vectors[: len(ids)], release)


def test_store_embeddings_into_a_release_leaves_the_default_table(_search_index):
    with database.get_sync_session() as session:
        before = session.scalar(select(func.count()).select_from(HadithEmbedding))
    _make_release("mv1")
    with database.get_sync_session() as session:
        assert session.scalar(select(func.count()).select_from(HadithEmbedding)) == before
        assert session.scalar(select(func.count()).select_from(embedding_table("mv1"))) == 3


def test_dense_search_reads_the_configured_release(_search_index, monkeypatch):
    query = np.array([0.0, 1.0, 0.0])
    with database.get_sync_session() as session:
        default = ranking.dense_search(session, query, "AR", top_k=3)
    assert max(default, key=default.get) == 2  # the default vectors: doc 2 is along y
    _make_release("mv1", vectors=np.array([[0.0, 1.0, 0.0], [1.0, 0, 0], [0, 0, 1.0]]))
    monkeypatch.setenv("EMBEDDINGS_RELEASE", "mv1")
    get_settings.cache_clear()
    with database.get_sync_session() as session:
        scores = ranking.dense_search(session, query, "AR", top_k=3, restrict={1, 2, 3})
    assert max(scores, key=scores.get) == 1  # in release mv1 doc 1 is along y


async def test_startup_check_passes_without_a_release(_search_index):
    await startup.check_embeddings_release()


async def test_startup_check_fails_for_a_missing_or_incomplete_release(_search_index, monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_RELEASE", "mv9")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="cannot be read"):
        await startup.check_embeddings_release()
    _make_release("mv9", ids=(1, 2))
    with pytest.raises(RuntimeError, match="2 vectors for 13 hadiths"):
        await startup.check_embeddings_release()
    _make_release("mv9", ids=(1, 2, 3))
    with database.get_sync_session() as session:
        store_embeddings(session, list(range(100, 110)), np.eye(10, 3), "mv9")
    await startup.check_embeddings_release()


def test_corpus_rebuild_drops_release_tables_and_their_registry(_search_index):
    _make_release("mv1")
    with database.get_sync_session() as session:
        session.add(EmbeddingSet(release="mv1", model_version="1", dim=3, created_at="t"))
        session.commit()
    database.drop_corpus_tables()
    names = inspect(database.get_sync_engine()).get_table_names()
    assert not [n for n in names if n.startswith("hadith_embeddings") or n == "embedding_sets"]
