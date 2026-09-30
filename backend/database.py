"""PostgreSQL access: async sessions for the API, sync sessions for build/evaluation scripts.

Set `DATABASE_URL` to a SQLAlchemy URL using the psycopg driver, e.g.
`postgresql+psycopg://user:password@host:5432/hadith`. The database needs the pgvector
extension available; `init_schema` turns it on.
"""

import os
from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import DDL, create_engine, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from models import (
    Annotation,
    AnnotationProgress,
    Annotator,
    Assignment,
    Base,
    Hadith,
    HadithEmbedding,
    HadithLength,
    KvPair,
    Posting,
    Term,
)

__all__ = [
    "Annotation",
    "AnnotationProgress",
    "Annotator",
    "Assignment",
    "Base",
    "CORPUS_TABLES",
    "Hadith",
    "HadithEmbedding",
    "HadithLength",
    "KvPair",
    "Posting",
    "Term",
    "database_url",
    "dispose_engines",
    "drop_corpus_tables",
    "get_async_engine",
    "get_hadith_row",
    "get_session",
    "get_sync_engine",
    "get_sync_session",
    "init_schema",
    "init_schema_sync",
    "now_iso",
    "read_hadiths_df",
]

# The corpus and everything derived from it; dropped together when the corpus is rebuilt.
CORPUS_TABLES = [
    Posting.__table__,
    Term.__table__,
    HadithLength.__table__,
    HadithEmbedding.__table__,
    Hadith.__table__,
]

_ENABLE_VECTOR = DDL("CREATE EXTENSION IF NOT EXISTS vector")

# Extra keyword arguments for both engines (tests swap in NullPool so engines don't outlive
# their event loop).
ENGINE_KWARGS: dict = {"pool_pre_ping": True}

# Engines are cached per URL so tests can point each test at its own schema.
_async_engines: dict[str, AsyncEngine] = {}
_sync_engines: dict = {}


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Example: "
            "postgresql+psycopg://user:password@localhost:5432/hadith"
        )
    return url


def get_async_engine() -> AsyncEngine:
    url = database_url()
    if url not in _async_engines:
        _async_engines[url] = create_async_engine(url, **ENGINE_KWARGS)
    return _async_engines[url]


def get_sync_engine():
    url = database_url()
    if url not in _sync_engines:
        _sync_engines[url] = create_engine(url, **ENGINE_KWARGS)
    return _sync_engines[url]


async def dispose_engines() -> None:
    """Close every pooled connection (tests and shutdown)."""
    for engine in _async_engines.values():
        await engine.dispose()
    for engine in _sync_engines.values():
        engine.dispose()
    _async_engines.clear()
    _sync_engines.clear()


def get_session() -> AsyncSession:
    """Use as `async with get_session() as session:` (commit explicitly)."""
    return async_sessionmaker(get_async_engine(), expire_on_commit=False)()


def get_sync_session() -> Session:
    """Sync session for build/evaluation scripts: `with get_sync_session() as session:`."""
    return Session(get_sync_engine(), expire_on_commit=False)


async def init_schema() -> None:
    """Enable pgvector and create any missing table (no migrations: existing tables are kept)."""
    async with get_async_engine().begin() as conn:
        await conn.execute(_ENABLE_VECTOR)
        await conn.run_sync(Base.metadata.create_all)


def init_schema_sync() -> None:
    with get_sync_engine().begin() as conn:
        conn.execute(_ENABLE_VECTOR)
        Base.metadata.create_all(conn)


def drop_corpus_tables() -> None:
    """Drop the corpus and its derived tables; `init_schema_sync` recreates them."""
    Base.metadata.drop_all(get_sync_engine(), tables=CORPUS_TABLES)


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
