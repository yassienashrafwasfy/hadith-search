import os
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from models import (
    Annotation,
    AnnotationProgress,
    Annotator,
    Assignment,
    Base,
    Hadith,
    KvPair,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "hadiths.db")

__all__ = [
    "ANNOTATION_TABLES",
    "Annotation",
    "AnnotationProgress",
    "Annotator",
    "Assignment",
    "Base",
    "DB_PATH",
    "Hadith",
    "KvPair",
    "drop_hadiths_table",
    "get_async_engine",
    "get_hadith_row",
    "get_session",
    "get_sync_engine",
    "get_sync_session",
    "init_annotation_tables",
    "init_hadiths_table",
    "init_kv_pairs_table",
    "now_iso",
    "read_hadiths_df",
]

ANNOTATION_TABLES = [
    Annotator.__table__,
    Assignment.__table__,
    Annotation.__table__,
    AnnotationProgress.__table__,
]

# Engines are keyed by DB_PATH so tests can point the module at a temp file.
# NullPool: SQLite connections are cheap and this keeps them from crossing event loops.
_async_engines: dict[str, AsyncEngine] = {}
_sync_engines: dict = {}


def get_async_engine() -> AsyncEngine:
    if DB_PATH not in _async_engines:
        _async_engines[DB_PATH] = create_async_engine(
            f"sqlite+aiosqlite:///{DB_PATH}", poolclass=NullPool
        )
    return _async_engines[DB_PATH]


def get_sync_engine():
    if DB_PATH not in _sync_engines:
        _sync_engines[DB_PATH] = create_engine(f"sqlite:///{DB_PATH}", poolclass=NullPool)
    return _sync_engines[DB_PATH]


def get_session() -> AsyncSession:
    """Use as `async with get_session() as session:` (commit explicitly)."""
    return async_sessionmaker(get_async_engine(), expire_on_commit=False)()


def get_sync_session() -> Session:
    """Sync session for build/evaluation scripts: `with get_sync_session() as session:`."""
    return Session(get_sync_engine(), expire_on_commit=False)


async def _create_tables(tables) -> None:
    async with get_async_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=tables)


async def init_annotation_tables() -> None:
    await _create_tables(ANNOTATION_TABLES)


async def init_kv_pairs_table() -> None:
    await _create_tables([KvPair.__table__])


def init_hadiths_table() -> None:
    Base.metadata.create_all(get_sync_engine(), tables=[Hadith.__table__])


def drop_hadiths_table() -> None:
    Base.metadata.drop_all(get_sync_engine(), tables=[Hadith.__table__])


def read_hadiths_df(*columns, order_by=None) -> pd.DataFrame:
    """Load hadiths as a DataFrame; pass model columns (e.g. Hadith.id) to select a subset."""
    stmt = select(*(columns or Hadith.__table__.c))
    if order_by is not None:
        stmt = stmt.order_by(order_by)
    return pd.read_sql(stmt, get_sync_engine())


def get_hadith_row(hadith_id: int) -> dict | None:
    """One hadith as a {column: value} dict, or None."""
    with get_sync_session() as session:
        hadith = session.get(Hadith, hadith_id)
        if hadith is None:
            return None
        return {c.name: getattr(hadith, c.name) for c in Hadith.__table__.c}


def now_iso():
    return datetime.now(timezone.utc).isoformat()
