"""ORM layer: helpers used by the build scripts, plus a guard that no raw SQL creeps back in."""

import pathlib
import re

import pandas as pd
import pytest

import database
from models import Hadith

_BACKEND = pathlib.Path(__file__).resolve().parent.parent / "backend"


def test_get_hadith_row(_patched_paths):
    row = database.get_hadith_row(2)
    assert row["Book"] == "Muslim"
    assert row["English_Text"] == "fasting is a shield"
    assert database.get_hadith_row(999) is None


def test_read_hadiths_df_all_and_subset(_patched_paths):
    everything = database.read_hadiths_df()
    assert set(everything["id"]) == {1, 2, 3}
    assert "Preprocessed_English_Matn" in everything.columns

    subset = database.read_hadiths_df(Hadith.id, Hadith.Book, order_by=Hadith.id.desc())
    assert list(subset.columns) == ["id", "Book"]
    assert subset["id"].tolist() == [3, 2, 1]


def test_hadith_records_convert_nan_and_ints():
    from scripts.data_creation import _hadith_records

    columns = [c.name for c in Hadith.__table__.c]
    row = {c: "x" for c in columns}
    row.update(id=1, Chapter_Number=3.0, Section_Number=float("nan"), Hadith_Number=7.0)
    for c in ("Has_English_Content", "Has_Arabic_Content", "Has_English_Matn", "Has_Arabic_Matn"):
        row[c] = 1
    (rec,) = _hadith_records(pd.DataFrame([row]))
    assert rec["Chapter_Number"] == 3 and isinstance(rec["Chapter_Number"], int)
    assert rec["Section_Number"] is None
    assert rec["Book"] == "x"


def test_create_database_roundtrip(_patched_paths):
    from sqlalchemy import Integer

    from scripts import data_creation

    frame = pd.DataFrame(
        [
            {c.name: (i if isinstance(c.type, Integer) else "t") for c in Hadith.__table__.c}
            for i in (1, 2)
        ]
    )
    data_creation.create_database(frame)
    assert database.read_hadiths_df()["id"].tolist() == [1, 2]


def test_build_all_checks(_patched_paths):
    from scripts import build_all

    assert build_all._has_columns(["Book", "Preprocessed_English"])
    assert not build_all._has_columns(["not_a_column"])
    assert build_all._row_count() == 3
    assert build_all._has_preprocessed_data()


def test_preprocess_run_updates_columns(_patched_paths, monkeypatch):
    from scripts import preprocess

    monkeypatch.setattr(preprocess, "preprocess_english", lambda t: t.upper())
    monkeypatch.setattr(preprocess, "preprocess_arabic", lambda t: t + "!")
    preprocess.run()
    row = database.get_hadith_row(2)
    assert row["Preprocessed_English"] == "FASTING IS A SHIELD"
    assert row["Preprocessed_Arabic_Matn"].endswith("!")


@pytest.mark.parametrize(
    "pattern",
    [
        r"\bsqlite3\b",
        r"\.execute\(\s*f?[\"']",  # execute("...raw sql...")
        r"read_sql\(\s*f?[\"']",
        r"\.to_sql\(",
        r"\bPRAGMA\b",
        r"\b(SELECT|INSERT INTO|UPDATE \w+ SET|DELETE FROM|CREATE TABLE|ALTER TABLE)\b",
    ],
)
def test_no_raw_sql_in_backend(pattern):
    offenders = []
    for path in _BACKEND.rglob("*.py"):
        if "data" in path.relative_to(_BACKEND).parts[:1]:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(pattern, line):
                offenders.append(f"{path.relative_to(_BACKEND)}:{n}: {line.strip()}")
    assert not offenders, "raw SQL found; use the ORM:\n" + "\n".join(offenders)


def test_build_all_index_and_embedding_checks(_search_index):
    from scripts import build_all

    assert build_all._has_index()
    assert not build_all._has_embeddings()  # only 3 of the 13 rows have vectors
    assert build_all._row_count() == 13


def test_build_all_checks_are_false_on_an_empty_database(_patched_paths):
    from scripts import build_all

    assert not build_all._has_index() and not build_all._has_embeddings()


def test_init_schema_keeps_extra_columns_and_tables(_pg_schema):
    """Blue/green relies on this: a newer schema's additions survive the older version starting."""
    from sqlalchemy import inspect, text

    database.init_schema_sync()
    with database.get_sync_engine().begin() as conn:
        conn.execute(text("ALTER TABLE hadiths ADD COLUMN added_later text"))
        conn.execute(text("CREATE TABLE added_table (id integer)"))
    database.init_schema_sync()  # what the older release does on startup
    inspector = inspect(database.get_sync_engine())
    assert "added_later" in {c["name"] for c in inspector.get_columns("hadiths")}
    assert "added_table" in inspector.get_table_names()
