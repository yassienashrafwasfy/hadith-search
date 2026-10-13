"""Quran store schema: catalog, per-source schemas, node rows, fixtures."""

import pytest
from quran_support import _simple
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from quran import models
from quran.db import add_nodes, create_source, drop_source, list_sources
from quran.models import check_source, node_table, schema_name
from quran.types import Level, SourceInfo


def test_source_name_is_validated():
    for bad in ["", "Quran", "a-b", "a;drop", "1abc", "x" * 41, 'a"b']:
        with pytest.raises(ValueError):
            check_source(bad)
    assert check_source("ibn_kathir") == "ibn_kathir"


def test_node_tables_are_per_schema(monkeypatch):
    monkeypatch.setattr(models, "SCHEMA_PREFIX", "p_")
    a, b = node_table("quran"), node_table("tafsir")
    assert a is node_table("quran")
    assert a.schema == "p_quran" and b.schema == "p_tafsir"
    assert {"id", "parent_id", "level", "text", "search_text", "summary", "embedding"} <= set(
        a.c.keys()
    )
    assert {"surah", "ayah_from", "ayah_to", "juz", "place", "author"} <= set(a.c.keys())


def test_empty_database_lists_no_sources(_quran_empty_session):
    assert list_sources(_quran_empty_session) == []


def test_create_and_list_sources(_quran_empty_session, _quran_sources):
    session = _quran_empty_session
    for info in _quran_sources.values():
        create_source(session, info)
    session.commit()
    assert list_sources(session) == [_quran_sources["quran"], _quran_sources["tafsir_mini"]]
    assert node_table("quran").schema in inspect(session.connection()).get_schema_names()


def test_create_source_twice_updates_catalog(_quran_empty_session):
    session = _quran_empty_session
    create_source(session, SourceInfo("quran", "old", ("ar",), ("surah",)))
    create_source(session, SourceInfo("quran", "new", ("ar", "en"), ("surah", "juz")))
    session.commit()
    (info,) = list_sources(session)
    assert info.description == "new" and info.languages == ("ar", "en")


def test_drop_source(_quran_session):
    drop_source(_quran_session, "tafsir_mini")
    _quran_session.commit()
    assert [s.name for s in list_sources(_quran_session)] == ["quran"]
    assert schema_name("tafsir_mini") not in inspect(_quran_session.connection()).get_schema_names()


def test_mini_corpus_shape(_quran_session):
    table = node_table("quran")
    counts = dict(
        _quran_session.execute(select(table.c.level, func.count()).group_by(table.c.level)).all()
    )
    assert counts == {"ayah": 17, "passage": 3, "surah": 3}
    leaf = _quran_session.execute(
        select(table).where(table.c.surah == 112, table.c.ayah_from == 1, table.c.level == "ayah")
    ).one()
    assert leaf.text == "قُلْ هُوَ ٱللَّهُ أَحَدٌ"  # original kept
    assert "ُ" not in leaf.search_text and leaf.search_text != leaf.text
    assert len(leaf.embedding) == 64
    parent = _quran_session.execute(
        select(table.c.level).where(table.c.id == leaf.parent_id)
    ).scalar_one()
    assert parent == "passage"


def test_cosine_search_finds_exact_text(_quran_session, _fake_encoder, _mini_corpus):
    table = node_table("quran")
    target = _mini_corpus[0]
    qvec = _fake_encoder.encode([_simple(target.text)])[0]
    best = _quran_session.execute(
        select(table.c.surah, table.c.ayah_from)
        .where(table.c.level == "ayah")
        .order_by(table.c.embedding.cosine_distance(qvec))
        .limit(1)
    ).one()
    assert (best.surah, best.ayah_from) == (1, 1)


def test_constraints(_quran_session):
    with pytest.raises(IntegrityError):
        add_nodes(_quran_session, "quran", [{"level": "chapter", "text": "x", "search_text": "x"}])
    _quran_session.rollback()
    with pytest.raises(IntegrityError):
        add_nodes(
            _quran_session,
            "quran",
            [{"level": Level.AYAH, "text": "x", "search_text": "x", "parent_id": 10**6}],
        )
    _quran_session.rollback()
    with pytest.raises(ValueError):
        add_nodes(_quran_session, "quran", [{"bogus": 1}])


def test_deleting_parent_cascades(_quran_session):
    table = node_table("quran")
    _quran_session.execute(table.delete().where(table.c.level == "surah"))
    assert _quran_session.scalar(select(func.count()).select_from(table)) == 0


def test_fake_encoder_is_deterministic_and_normalised(_fake_encoder):
    one, two = _fake_encoder.encode(["a", "a"]), _fake_encoder.encode(["b"])
    assert one[0] == one[1] != two[0]
    assert len(one[0]) == 64
    assert sum(v * v for v in one[0]) == pytest.approx(1.0)


def test_sources_are_isolated(_quran_session):
    tafsir = node_table("tafsir_mini")
    assert _quran_session.scalar(select(func.count()).select_from(tafsir)) == 4
