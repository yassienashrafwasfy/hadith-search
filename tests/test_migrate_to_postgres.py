import pytest
from sqlalchemy import Column, Integer, MetaData, Table, Text, create_engine, insert, select

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
    Posting,
)
from scripts import migrate_to_postgres as mig

OLD_TABLES = [m.__table__ for m in mig.TABLES]

# The flat `hadiths` table an old install had: chapter titles, book and grade copied on each row.
_OLD_HADITHS = Table(
    "hadiths",
    MetaData(),
    Column("id", Integer, primary_key=True),
    *(
        Column(name, Text)
        for name in (
            "Book",
            "LK_Book",
            "Chapter_English",
            "Chapter_Arabic",
            "Chapter_Title_English",
            "Chapter_Title_Arabic",
            "English_Text",
            "English_Hadith",
            "Grade",
            "English_Grade",
            "Preprocessed_English_Matn",
            "Preprocessed_Arabic_Matn",
        )
    ),
    Column("Chapter_Number", Integer),
)


def _old_install(tmp_path):
    """A SQLite file and .npy files shaped like a pre-PostgreSQL install."""
    path = tmp_path / "hadiths.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine, tables=OLD_TABLES)
    _OLD_HADITHS.create(engine)
    with engine.begin() as conn:
        conn.execute(
            insert(_OLD_HADITHS),
            [
                {
                    "id": i,
                    "Book": "Bukhari",
                    "LK_Book": "Bukhari",
                    "Chapter_Number": 1,
                    "Chapter_English": "Prayer",
                    "Chapter_Title_Arabic": "الصلاة",
                    "English_Hadith": f"text {i}",
                    "Grade": "Sahih",
                    "Preprocessed_English_Matn": f"prayer word{i}",
                    "Preprocessed_Arabic_Matn": "صلاه كلمه",
                }
                for i in (4, 9)
            ],
        )
        conn.execute(
            insert(Annotator),
            [
                {"id": 7, "username": "alice", "password_hash": "h", "password_salt": "s"}
                | {"created_at": "t"}
            ],
        )
        conn.execute(
            insert(Annotation),
            [
                {"annotator_id": 7, "query_id": "q1", "hadith_id": 4, "label": 2}
                | {"created_at": "t", "updated_at": "t"}
            ],
        )
        conn.execute(
            insert(KvPair),
            [
                {
                    "id": 12,
                    "topic": "t",
                    "language": "en",
                    "concept_en": "c",
                    "concept_ar": "ك",
                    "entity_en": "e",
                    "entity_ar": "ع",
                    "hadith_id": 4,
                    "status": "pending",
                    "created_at": "t",
                }
            ],
        )
    engine.dispose()
    return path


@pytest.fixture
def _migrated(_pg_schema, tmp_path):
    counts = mig.migrate(str(_old_install(tmp_path)), str(tmp_path))
    return counts


def test_copies_every_table(_migrated):
    assert _migrated == {
        "hadiths": 2,
        "annotators": 1,
        "assignments": 0,
        "annotations": 1,
        "annotation_progress": 0,
        "kv_pairs": 1,
    }
    with database.get_sync_session() as session:
        assert session.get(Annotator, 7).username == "alice"
        assert session.get(Hadith, 9).Book == "Bukhari"


def test_old_flat_hadiths_are_split_into_the_new_tables(_migrated):
    with database.get_sync_session() as session:
        hadith = session.get(Hadith, 9)
        assert (hadith.English_Text, hadith.English_Grade) == ("text 9", "Sahih")
        chapter = session.get(Chapter, ("Bukhari", 1))
        assert (chapter.title_english, chapter.title_arabic) == ("Prayer", "الصلاة")
        assert session.get(HadithPreprocessed, 4).Preprocessed_Arabic_Matn == "صلاه كلمه"


def test_embeddings_and_index_are_built(_migrated):
    with database.get_sync_session() as session:
        assert session.query(HadithEmbedding).count() == 0  # old E5 vectors are not copied
        assert session.query(Posting).filter_by(language="EN", term="prayer").count() == 2


def test_new_rows_get_ids_past_the_copied_ones(_migrated):
    with database.get_sync_session() as session:
        session.add(Annotator(username="bob", password_hash="h", password_salt="s", created_at="t"))
        session.add(
            KvPair(
                topic="t",
                language="en",
                concept_en="c",
                concept_ar="c",
                entity_en="e",
                entity_ar="e",
                hadith_id=4,
                created_at="t",
            )
        )
        session.commit()
        assert session.scalar(select(Annotator.id).where(Annotator.username == "bob")) == 8
        assert max(session.scalars(select(KvPair.id))) == 13


def test_refuses_to_copy_into_a_used_database(_migrated, tmp_path):
    with pytest.raises(RuntimeError, match="not empty"):
        mig.migrate(str(tmp_path / "hadiths.db"), str(tmp_path))


def test_missing_sqlite_file(_pg_schema, tmp_path):
    with pytest.raises(FileNotFoundError):
        mig.migrate(str(tmp_path / "nope.db"), str(tmp_path))
