"""Create, list and fill the per-source Quran stores (sync sessions, ORM and DDL constructs only)."""

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import DDL, delete, func, insert, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from quran.models import NODE_COLUMNS, Source, check_source, node_table, schema_name  # noqa: F401
from quran.types import SourceInfo

_ENABLE_VECTOR = DDL("CREATE EXTENSION IF NOT EXISTS vector")
CATALOG_LOCK = 7_310  # one source creator at a time (transaction-scoped advisory lock)


def init_catalog(session: Session) -> None:
    """Create the `sources` catalog if missing (additive)."""
    session.execute(select(func.pg_advisory_xact_lock(CATALOG_LOCK)))
    session.execute(_ENABLE_VECTOR)
    Source.__table__.create(session.connection(), checkfirst=True)


def create_source(session: Session, info: SourceInfo) -> None:
    """Create the schema and node table of a source and register it; no-op for existing parts.

    Re-running with new metadata updates the catalog row. The caller commits.
    """
    check_source(info.name)
    init_catalog(session)
    conn = session.connection()
    session.execute(CreateSchema(schema_name(info.name), if_not_exists=True))
    node_table(info.name).create(conn, checkfirst=True)
    row = {
        "name": info.name,
        "description": info.description,
        "languages": list(info.languages),
        "fields": list(info.fields),
        "authors": list(info.authors),
    }
    existing = session.get(Source, info.name)
    if existing is None:
        session.add(Source(**row))
    else:
        for key, value in row.items():
            setattr(existing, key, value)
    session.flush()


def drop_source(session: Session, name: str) -> None:
    """Remove a source: its schema (with all nodes) and its catalog row."""
    init_catalog(session)
    session.execute(delete(Source).where(Source.name == check_source(name)))
    session.execute(DropSchema(schema_name(name), cascade=True, if_exists=True))


def list_sources(session: Session) -> list[SourceInfo]:
    """Every registered source, ordered by name; empty when the catalog does not exist yet."""
    if not inspect(session.connection()).has_table(Source.__tablename__):
        return []
    rows = session.scalars(select(Source).order_by(Source.name))
    return [
        SourceInfo(
            name=r.name,
            description=r.description,
            languages=tuple(r.languages),
            fields=tuple(r.fields),
            authors=tuple(r.authors or ()),
        )
        for r in rows
    ]


def add_nodes(session: Session, source: str, rows: Iterable[Mapping[str, Any]]) -> list[int]:
    """Insert nodes (keys from `NODE_COLUMNS` except id) and return their ids in input order.

    `level` may be a `Level`; `place` a `Place`. A row's `parent_id` must already exist, so insert
    parents first (the caller commits).
    """
    table = node_table(source)
    ids = []
    for row in rows:
        unknown = set(row) - set(NODE_COLUMNS)
        if unknown:
            raise ValueError(f"unknown node columns: {sorted(unknown)}")
        values = {k: (v.value if hasattr(v, "value") else v) for k, v in row.items()}
        statement = insert(table).values(**values).returning(table.c.id)
        ids.append(session.execute(statement).scalar_one())
    return ids
