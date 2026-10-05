"""PostgreSQL access: async sessions for the API, sync sessions for build/evaluation scripts.

Set `DATABASE_URL` to a SQLAlchemy URL using the psycopg driver, e.g.
`postgresql+psycopg://user:password@host:5432/hadith`. The database needs the pgvector
and pg_trgm extensions available; `init_schema` turns them on.
"""

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import DDL, ForeignKeyConstraint, create_engine, func, insert, inspect, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session
from sqlalchemy.schema import AddConstraint

from models import (
    HADITH_CHAPTER,
    Annotation,
    AnnotationProgress,
    Annotator,
    Assignment,
    Base,
    Book,
    Chapter,
    EmbeddingSet,
    Hadith,
    HadithEmbedding,
    HadithExactText,
    HadithLength,
    HadithPreprocessed,
    KvPair,
    Posting,
    Term,
)
from models.embedding_sets import PREFIX as RELEASE_TABLE_PREFIX
from settings import get_settings

__all__ = [
    "Annotation",
    "AnnotationProgress",
    "Annotator",
    "Assignment",
    "Base",
    "Book",
    "CORPUS_TABLES",
    "Chapter",
    "EmbeddingSet",
    "Hadith",
    "HadithEmbedding",
    "HadithExactText",
    "HadithLength",
    "HadithPreprocessed",
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
    "insert_hadith_rows",
    "init_schema_sync",
    "now_iso",
    "read_hadiths_df",
    "restore_hadith_references",
]

# The corpus and everything derived from it; dropped together when the corpus is rebuilt.
CORPUS_TABLES = [
    HadithExactText.__table__,
    Posting.__table__,
    Term.__table__,
    HadithLength.__table__,
    HadithEmbedding.__table__,
    EmbeddingSet.__table__,
    HadithPreprocessed.__table__,
    Hadith.__table__,
    Chapter.__table__,
    Book.__table__,
]

# annotations and kv_pairs point at hadiths.id but are not rebuilt with the corpus.
_HADITH_REFERENCES = [
    constraint
    for model in (Annotation, KvPair)
    for constraint in model.__table__.constraints
    if isinstance(constraint, ForeignKeyConstraint)
    and constraint.referred_table is Hadith.__table__
]

_PREPROCESSED_COLUMNS = [c.name for c in HadithPreprocessed.__table__.c if c.name != "hadith_id"]

_ENABLE_VECTOR = DDL("CREATE EXTENSION IF NOT EXISTS vector")
_ENABLE_TRGM = DDL("CREATE EXTENSION IF NOT EXISTS pg_trgm")  # exact search and suggestions

# Transaction-scoped advisory lock keys (any app-wide constants; unrelated to table names).
SCHEMA_LOCK = 7_302  # one schema initialiser at a time (blue and green can start together)
ASSIGNMENT_LOCK = 7_303  # one sign-up at a time hands out query assignments

# Extra keyword arguments for both engines (tests swap in NullPool so engines don't outlive
# their event loop). Without a `poolclass`, the pool is sized from settings (`pool_kwargs`).
ENGINE_KWARGS: dict = {"pool_pre_ping": True}


def pool_kwargs() -> dict:
    """Reuse connections: one per running search, a little overflow, recycled before they go stale."""
    if "poolclass" in ENGINE_KWARGS:
        return dict(ENGINE_KWARGS)
    settings = get_settings()
    return {
        **ENGINE_KWARGS,
        "pool_size": settings.db_pool_size or settings.search_max_concurrent,
        "max_overflow": settings.db_pool_overflow,
        "pool_recycle": settings.db_pool_recycle_seconds,
    }


# Engines are cached per URL so tests can point each test at its own schema.
_async_engines: dict[str, AsyncEngine] = {}
_sync_engines: dict = {}


def database_url() -> str:
    url = get_settings().database_url
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Example: "
            "postgresql+psycopg://user:password@localhost:5432/hadith"
        )
    return url


def get_async_engine() -> AsyncEngine:
    url = database_url()
    if url not in _async_engines:
        _async_engines[url] = create_async_engine(url, **pool_kwargs())
    return _async_engines[url]


def get_sync_engine():
    url = database_url()
    if url not in _sync_engines:
        _sync_engines[url] = create_engine(url, **pool_kwargs())
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


# Tables whose indexes `init_schema*` adds to an existing database (create_all skips existing
# tables). They are small and rarely written, so the build is quick and additive.
INDEXED_LATER = ("annotations", "assignments", "kv_pairs")


def _create_missing_indexes(conn) -> None:
    for table in Base.metadata.sorted_tables:
        if table.name in INDEXED_LATER:
            for index in table.indexes:
                index.create(conn, checkfirst=True)


async def init_schema() -> None:
    """Enable pgvector and pg_trgm and create any missing table (no migrations: existing tables are kept)."""
    async with get_async_engine().begin() as conn:
        await conn.execute(select(func.pg_advisory_xact_lock(SCHEMA_LOCK)))
        await conn.execute(_ENABLE_VECTOR)
        await conn.execute(_ENABLE_TRGM)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_create_missing_indexes)


def init_schema_sync() -> None:
    with get_sync_engine().begin() as conn:
        conn.execute(select(func.pg_advisory_xact_lock(SCHEMA_LOCK)))
        conn.execute(_ENABLE_VECTOR)
        conn.execute(_ENABLE_TRGM)
        Base.metadata.create_all(conn)
        _create_missing_indexes(conn)


def drop_corpus_tables(bind=None) -> None:
    """Drop the corpus and its derived tables; `init_schema_sync` recreates them.

    Pass a connection to make the drop part of the caller's transaction (DDL is transactional
    in PostgreSQL), so a failure later in that transaction brings the old tables back.
    The drop cascades, which removes the foreign keys annotations and kv_pairs have on
    `hadiths`; call `restore_hadith_references` once the new corpus is loaded.
    """
    if bind is None:
        with get_sync_engine().begin() as conn:
            drop_corpus_tables(conn)
        return
    # The per-release vector tables (scripts/promote_model.py) hold vectors of the old corpus.
    stale = [t for t in inspect(bind).get_table_names() if t.startswith(RELEASE_TABLE_PREFIX)]
    for name in [*stale, *(table.name for table in CORPUS_TABLES)]:
        bind.execute(DDL(f'DROP TABLE IF EXISTS "{name}" CASCADE'))


def restore_hadith_references(bind) -> None:
    """Add back the foreign keys from annotations and kv_pairs to the rebuilt `hadiths`.

    Fails (and so rolls back the caller's transaction) when a stored annotation or KV pair
    points at a hadith id the new corpus does not have.
    """
    for constraint in _HADITH_REFERENCES:
        bind.execute(AddConstraint(constraint))


def insert_hadith_rows(bind, rows: list[dict]) -> None:
    """Insert hadiths given as flat dicts; the book, chapter and preprocessed parts are split off.

    A flat row has the `hadiths` columns plus `LK_Book`, `Chapter_Title_English`,
    `Chapter_Title_Arabic` and (optionally) the `Preprocessed_*` texts. Two rows for the same
    chapter must agree on its titles, otherwise the chapter would silently lose one of them.
    """
    books: dict[str, str | None] = {}
    chapters: dict[tuple[str, int], tuple] = {}
    preprocessed = []
    for row in rows:
        book, number = row.get("Book"), row.get("Chapter_Number")
        if book is not None:
            books.setdefault(book, row.get("LK_Book"))
        if book is not None and number is not None:
            titles = (row.get("Chapter_Title_English"), row.get("Chapter_Title_Arabic"))
            if chapters.setdefault((book, number), titles) != titles:
                raise ValueError(f"Chapter {number} of {book} has two different titles")
        if any(row.get(name) is not None for name in _PREPROCESSED_COLUMNS):
            preprocessed.append(
                {"hadith_id": row["id"]} | {n: row.get(n) for n in _PREPROCESSED_COLUMNS}
            )
    if books:
        bind.execute(insert(Book), [{"book": book, "lk_book": lk} for book, lk in books.items()])
    if chapters:
        bind.execute(
            insert(Chapter),
            [
                {"book": b, "chapter_number": n, "title_english": en, "title_arabic": ar}
                for (b, n), (en, ar) in chapters.items()
            ],
        )
    columns = {c.name for c in Hadith.__table__.c}
    bind.execute(insert(Hadith), [{k: v for k, v in row.items() if k in columns} for row in rows])
    if preprocessed:
        bind.execute(insert(HadithPreprocessed), preprocessed)


def _flat_columns():
    """Every `hadiths` column plus the chapter, book and preprocessed columns joined back in."""
    return [
        *Hadith.__table__.c,
        Book.lk_book.label("LK_Book"),
        Chapter.title_english.label("Chapter_Title_English"),
        Chapter.title_arabic.label("Chapter_Title_Arabic"),
        *(HadithPreprocessed.__table__.c[name] for name in _PREPROCESSED_COLUMNS),
    ]


def _flat_from():
    return (
        Hadith.__table__.outerjoin(Chapter.__table__, HADITH_CHAPTER)
        .outerjoin(Book.__table__, Book.book == Hadith.Book)
        .outerjoin(HadithPreprocessed.__table__, HadithPreprocessed.hadith_id == Hadith.id)
    )


def read_hadiths_df(*columns, order_by=None, bind=None) -> pd.DataFrame:
    """Load hadiths as a DataFrame; pass model columns (e.g. Hadith.id) to select a subset.

    With no columns you get the flat view: `hadiths` joined with its chapter, book and
    preprocessed texts. `bind` is a connection to read through (to see its uncommitted rows);
    default: the engine.
    """
    stmt = select(*(columns or _flat_columns()))
    if not columns:
        stmt = stmt.select_from(_flat_from())
        order_by = Hadith.id if order_by is None else order_by  # joins give no stable order
    if order_by is not None:
        stmt = stmt.order_by(order_by)
    return pd.read_sql(stmt, bind or get_sync_engine())


# Column names the API used to return for a hadith, now computed from the single stored column.
_LEGACY_ALIASES = {
    "English_Hadith": "English_Text",
    "Arabic_Hadith": "Arabic_Text",
    "Grade": "English_Grade",
    "Chapter_English": "Chapter_Title_English",
    "Chapter_Arabic": "Chapter_Title_Arabic",
}


def get_hadith_row(hadith_id: int) -> dict | None:
    """One hadith as a {column: value} dict (flat view plus the old duplicate names), or None."""
    stmt = select(*_flat_columns()).select_from(_flat_from()).where(Hadith.id == hadith_id)
    with get_sync_session() as session:
        row = session.execute(stmt).mappings().first()
    if row is None:
        return None
    flat = dict(row)
    return flat | {alias: flat[source] for alias, source in _LEGACY_ALIASES.items()}


def now_iso():
    return datetime.now(timezone.utc).isoformat()
