"""One-time copy of an old SQLite + .npy install into PostgreSQL.

    DATABASE_URL=postgresql+psycopg://... python scripts/migrate_to_postgres.py [--data-dir DIR]

Copies the tables from `hadiths.db` (hadiths, annotators, assignments, annotations, progress,
KV pairs), loads the `.npy` embeddings, then rebuilds the BM25 index from the preprocessed matn
columns. The `.pkl` index files are not read: they are built from those same columns, and
unpickling is only safe for files you made yourself. Run it against an empty database; it stops
if the hadiths table already has rows.
"""

import argparse
import os

import numpy as np
from sqlalchemy import MetaData, Table, create_engine, func, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from database import get_sync_session, init_schema_sync, read_hadiths_df
from models import Annotation, AnnotationProgress, Annotator, Assignment, Hadith, KvPair
from scripts.build_inverted_index import write_index
from scripts.embedding_store import store_embeddings

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
BATCH = 5000

# Parents before children; the copy order also keeps foreign keys satisfied.
TABLES = [Hadith, Annotator, Assignment, Annotation, AnnotationProgress, KvPair]
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


def reset_sequences(session: Session) -> None:
    """Point each serial id at the largest copied id so new rows do not collide."""
    for model in SERIAL_TABLES:
        table = model.__table__
        sequence = func.pg_get_serial_sequence(table.name, "id")
        session.execute(select(func.setval(sequence, func.coalesce(func.max(table.c.id), 1))))


def copy_embeddings(session: Session, data_dir: str) -> int:
    """Load `{english,arabic}_embeddings.npy` (row-aligned with `hadith_ids.npy`), if present."""
    ids_path = os.path.join(data_dir, "hadith_ids.npy")
    if not os.path.exists(ids_path):
        return 0
    ids = np.load(ids_path, allow_pickle=False).tolist()
    stored = 0
    for language, name in (("EN", "english"), ("AR", "arabic")):
        path = os.path.join(data_dir, f"{name}_embeddings.npy")
        if os.path.exists(path):
            stored += store_embeddings(session, ids, np.load(path, allow_pickle=False), language)
    return stored


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
            for model in TABLES:
                counts[model.__tablename__] = copy_table(source, session, model)
            reset_sequences(session)
            session.commit()
            counts["embedding_vectors"] = copy_embeddings(session, data_dir)
            write_index(session, read_hadiths_df())
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
