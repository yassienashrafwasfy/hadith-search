"""One-time copy of an old SQLite + .npy install into PostgreSQL.

    DATABASE_URL=postgresql+psycopg://... python scripts/migrate_to_postgres.py [--data-dir DIR]

Copies the tables from `hadiths.db` (hadiths, annotators, assignments, annotations, progress,
KV pairs), then rebuilds the BM25 index from the preprocessed matn columns. The old `.npy`
embeddings (E5) are not copied: semantic search now uses the Arabic ONNX encoder, so run
`scripts/build_embeddings.py` afterwards. The `.pkl` index files are not read: they are built from those same columns, and
unpickling is only safe for files you made yourself. Run it against an empty database; it stops
if the hadiths table already has rows.
"""

import argparse
import os

from sqlalchemy import MetaData, Table, create_engine, func, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from database import get_sync_session, init_schema_sync, insert_hadith_rows, read_hadiths_df
from models import Annotation, AnnotationProgress, Annotator, Assignment, Hadith, KvPair
from scripts.build_inverted_index import write_index

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
BATCH = 5000

# Parents before children; the copy order also keeps foreign keys satisfied. `hadiths` is copied
# first by `copy_hadiths`, because the old flat table is split into several new ones.
TABLES = [Annotator, Assignment, Annotation, AnnotationProgress, KvPair]

# Columns an older install had instead of the single stored column: new name <- old name.
_OLD_NAMES = {
    "Chapter_Title_English": "Chapter_English",
    "Chapter_Title_Arabic": "Chapter_Arabic",
    "English_Grade": "Grade",
    "English_Text": "English_Hadith",
    "Arabic_Text": "Arabic_Hadith",
}
# Tables whose primary key is a generated id: the copied ids need the sequence moved past them.
SERIAL_TABLES = [Annotator, Assignment, KvPair]


def _source_table(source: Engine, name: str) -> Table | None:
    metadata = MetaData()
    metadata.reflect(source, only=[name])
    return metadata.tables.get(name)


def copy_table(source: Engine, session: Session, model) -> int:
    """Copy the columns both databases have; a missing source table counts as empty."""
    table = _source_table(source, model.__tablename__)
    if table is None:
        return 0
    columns = [c for c in model.__table__.c if c.name in table.c]
    copied = 0
    with source.connect() as conn:
        result = conn.execute(select(*(table.c[c.name] for c in columns)))
        while batch := result.fetchmany(BATCH):
            session.execute(insert(model), [dict(zip([c.name for c in columns], r)) for r in batch])
            copied += len(batch)
    return copied


def copy_hadiths(source: Engine, session: Session) -> int:
    """Copy the old flat `hadiths` table into books, chapters, hadiths and hadith_preprocessed."""
    table = _source_table(source, "hadiths")
    if table is None:
        return 0
    with source.connect() as conn:
        rows = [dict(row) for row in conn.execute(select(table)).mappings()]
    for row in rows:
        for new, old in _OLD_NAMES.items():
            if row.get(new) is None and row.get(old) is not None:
                row[new] = row[old]
    insert_hadith_rows(session, rows)
    return len(rows)


def reset_sequences(session: Session) -> None:
    """Point each serial id at the largest copied id so new rows do not collide."""
    for model in SERIAL_TABLES:
        table = model.__table__
        sequence = func.pg_get_serial_sequence(table.name, "id")
        session.execute(select(func.setval(sequence, func.coalesce(func.max(table.c.id), 1))))


def migrate(sqlite_path: str, data_dir: str = DATA_DIR) -> dict[str, int]:
    if not os.path.exists(sqlite_path):
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")
    init_schema_sync()
    source = create_engine(f"sqlite:///{sqlite_path}")
    counts = {}
    try:
        with get_sync_session() as session:
            if session.scalar(select(func.count()).select_from(Hadith)):
                raise RuntimeError("The PostgreSQL hadiths table is not empty; refusing to copy.")
            counts["hadiths"] = copy_hadiths(source, session)
            for model in TABLES:
                counts[model.__tablename__] = copy_table(source, session, model)
            reset_sequences(session)
            # Same transaction as the copy: the index is built from the uncommitted hadiths and
            # everything commits together, so a failure leaves the database empty, not half-filled.
            write_index(session, read_hadiths_df(bind=session.connection()), commit=False)
            session.commit()
    finally:
        source.dispose()
    return counts


def run():
    parser = argparse.ArgumentParser(description="Copy an old SQLite install into PostgreSQL.")
    parser.add_argument("--data-dir", default=DATA_DIR)
    parser.add_argument("--sqlite", default=None, help="Path to hadiths.db (default: DATA_DIR)")
    args = parser.parse_args()
    sqlite_path = args.sqlite or os.path.join(args.data_dir, "hadiths.db")
    for name, count in migrate(sqlite_path, args.data_dir).items():
        print(f"{name:20s} {count}")


if __name__ == "__main__":
    run()
