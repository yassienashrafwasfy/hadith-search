"""The schema is in third normal form: each fact is stored once, and the keys say what depends on what."""

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

import database
from models import (
    Annotation,
    Annotator,
    Base,
    Chapter,
    Hadith,
    HadithEmbedding,
    HadithPreprocessed,
    KvPair,
)

# Columns that used to repeat another column of the same row, or a fact about the chapter or book.
REMOVED_FROM_HADITHS = {
    "LK_Book",
    "Chapter_Title_English",
    "Chapter_Title_Arabic",
    "Chapter_English",
    "Chapter_Arabic",
    "English_Hadith",
    "Arabic_Hadith",
    "Grade",
    "Preprocessed_English",
    "Preprocessed_Arabic",
    "Preprocessed_English_Isnad",
    "Preprocessed_Arabic_Isnad",
    "Preprocessed_English_Matn",
    "Preprocessed_Arabic_Matn",
}


def _foreign_keys(table: str) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    """(columns, referenced table, referenced columns) for every foreign key of the table."""
    return {
        (
            tuple(fk.parent.name for fk in c.elements),
            c.referred_table.name,
            tuple(fk.column.name for fk in c.elements),
        )
        for c in Base.metadata.tables[table].foreign_key_constraints
    }


def test_repeated_columns_are_gone_from_hadiths():
    assert not REMOVED_FROM_HADITHS & {c.name for c in Hadith.__table__.c}


def test_embeddings_hold_only_the_arabic_vector():
    assert {c.name for c in HadithEmbedding.__table__.c} == {"hadith_id", "arabic"}


def test_kv_pairs_no_longer_copy_the_hadith_text():
    assert not {"hadith_en", "hadith_ar"} & {c.name for c in KvPair.__table__.c}


def test_the_split_out_facts_live_in_their_own_tables():
    tables = Base.metadata.tables
    assert [c.name for c in tables["books"].primary_key] == ["book"]
    assert [c.name for c in tables["chapters"].primary_key] == ["book", "chapter_number"]
    assert [c.name for c in tables["hadith_preprocessed"].primary_key] == ["hadith_id"]
    assert {"title_english", "title_arabic"} <= {c.name for c in Chapter.__table__.c}
    assert "Preprocessed_Arabic_Matn" in {c.name for c in HadithPreprocessed.__table__.c}


def test_foreign_keys_tie_every_fact_to_its_owner():
    assert (("Book", "Chapter_Number"), "chapters", ("book", "chapter_number")) in _foreign_keys(
        "hadiths"
    )
    assert (("Book",), "books", ("book",)) in _foreign_keys("hadiths")
    assert (("book",), "books", ("book",)) in _foreign_keys("chapters")
    for table in ("hadith_preprocessed", "annotations", "kv_pairs"):
        assert (("hadith_id",), "hadiths", ("id",)) in _foreign_keys(table), table


def test_sections_stay_on_hadiths():
    """(Book, Section_Number) does not fix the section titles (735 conflicts in the real data)."""
    assert "sections" not in Base.metadata.tables
    assert {"Section_Number", "Section_English", "Section_Arabic"} <= {
        c.name for c in Hadith.__table__.c
    }


def test_database_has_the_new_tables_and_columns(_patched_paths):
    inspector = inspect(database.get_sync_engine())
    assert {"books", "chapters", "hadith_preprocessed"} <= set(inspector.get_table_names())
    assert not REMOVED_FROM_HADITHS & {c["name"] for c in inspector.get_columns("hadiths")}
    assert {"hadith_en", "hadith_ar"}.isdisjoint(
        c["name"] for c in inspector.get_columns("kv_pairs")
    )


def test_chapter_titles_are_stored_once_per_chapter(_patched_paths):
    with database.get_sync_session() as session:
        database.insert_hadith_rows(
            session,
            [
                {"id": 50, "Book": "Tirmidhi", "Chapter_Number": 9, "Chapter_Title_English": "X"},
                {"id": 51, "Book": "Tirmidhi", "Chapter_Number": 9, "Chapter_Title_English": "X"},
            ],
        )
        session.commit()
        assert session.scalars(
            select(Chapter.title_english).where(Chapter.chapter_number == 9)
        ).all() == ["X"]


def test_two_titles_for_one_chapter_are_refused(_patched_paths):
    rows = [
        {"id": 60, "Book": "Tirmidhi", "Chapter_Number": 9, "Chapter_Title_English": "X"},
        {"id": 61, "Book": "Tirmidhi", "Chapter_Number": 9, "Chapter_Title_English": "Y"},
    ]
    with database.get_sync_session() as session, pytest.raises(ValueError, match="two different"):
        database.insert_hadith_rows(session, rows)


def test_a_hadith_needs_its_chapter(_patched_paths):
    with database.get_sync_session() as session:
        session.add(Hadith(id=70, Book="Muslim", Chapter_Number=999))
        with pytest.raises(IntegrityError):
            session.commit()


def _insert_annotator(session) -> int:
    annotator = Annotator(username="a", password_hash="h", password_salt="s", created_at="t")
    session.add(annotator)
    session.commit()
    return annotator.id


def test_an_annotation_needs_an_existing_hadith(_patched_paths):
    with database.get_sync_session() as session:
        annotator_id = _insert_annotator(session)
        session.add(
            Annotation(
                annotator_id=annotator_id,
                query_id="q",
                hadith_id=999,
                label=1,
                created_at="t",
                updated_at="t",
            )
        )
        with pytest.raises(IntegrityError, match="annotations_hadith_id_fkey"):
            session.commit()


def test_a_kv_pair_needs_an_existing_hadith(_patched_paths):
    with database.get_sync_session() as session:
        session.add(
            KvPair(
                topic="t",
                language="en",
                concept_en="c",
                concept_ar="c",
                entity_en="e",
                entity_ar="e",
                hadith_id=999,
                created_at="t",
            )
        )
        with pytest.raises(IntegrityError, match="kv_pairs_hadith_id_fkey"):
            session.commit()


def test_rebuilding_the_corpus_keeps_the_references_checked(_patched_paths):
    """Dropping hadiths cascades away the foreign keys; the rebuild must put them back."""
    from test_database import _corpus_frame

    from scripts import data_creation

    data_creation.create_database(_corpus_frame([1, 2]))
    names = {fk["name"] for fk in inspect(database.get_sync_engine()).get_foreign_keys("kv_pairs")}
    assert names, "kv_pairs lost its foreign key on hadiths"
