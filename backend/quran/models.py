"""Tables of the Quran stores: a `sources` catalog and one node table per source.

Each source lives in its own PostgreSQL schema (`node_table(source)`), all with the same columns,
so a tafsir can be added or dropped without touching the others. Nothing here is in
`models.orm.Base.metadata`, so `init_schema` never creates these by accident. The Tanzil text in
`text` is stored byte for byte; embeddings and search use `search_text`.
"""

import re
from functools import lru_cache

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from quran.types import Level

SOURCE_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
# Prepended to the schema of every source. Empty in production; tests set a unique one so
# parallel workers do not share schemas.
SCHEMA_PREFIX = ""


class QuranBase(DeclarativeBase):
    metadata = MetaData()


class Source(QuranBase):
    """One row per store; the router reads these."""

    __tablename__ = "quran_sources"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    description: Mapped[str] = mapped_column(Text)
    languages: Mapped[list[str]] = mapped_column(ARRAY(String))
    fields: Mapped[list[str]] = mapped_column(ARRAY(String))
    authors: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)


def check_source(name: str) -> str:
    """The name is also a schema name, so only lowercase letters, digits and underscores."""
    if not SOURCE_NAME.fullmatch(name):
        raise ValueError(f"invalid source name: {name!r}")
    return name


def schema_name(source: str) -> str:
    return SCHEMA_PREFIX + check_source(source)


_node_metadata = MetaData()


def node_table(source: str) -> Table:
    """The node table of `source` (schema = source name). Not created here; see `db.create_source`."""
    return _build(schema_name(source))


@lru_cache(maxsize=None)
def _build(schema: str) -> Table:
    table = Table(
        "nodes",
        _node_metadata,
        Column("id", Integer, primary_key=True),
        Column(
            "parent_id",
            Integer,
            ForeignKey(f"{schema}.nodes.id", ondelete="CASCADE"),
            nullable=True,
        ),
        Column("level", String(8), nullable=False),
        Column("text", Text, nullable=False),
        Column("search_text", Text, nullable=False),
        Column("summary", Text, nullable=True),
        Column("embedding", VECTOR(), nullable=True),
        Column("surah", Integer, nullable=True),
        Column("ayah_from", Integer, nullable=True),
        Column("ayah_to", Integer, nullable=True),
        Column("juz", Integer, nullable=True),
        Column("place", String(8), nullable=True),
        Column("author", String(80), nullable=True),
        CheckConstraint("level in (%s)" % ", ".join(f"'{v.value}'" for v in Level), name="level"),
        CheckConstraint("place is null or place in ('meccan', 'medinan')", name="place"),
        schema=schema,
    )
    Index("ix_nodes_parent", table.c.parent_id)
    Index("ix_nodes_position", table.c.level, table.c.surah, table.c.ayah_from)
    return table


NODE_COLUMNS = (
    "parent_id",
    "level",
    "text",
    "search_text",
    "summary",
    "embedding",
    "surah",
    "ayah_from",
    "ayah_to",
    "juz",
    "place",
    "author",
)
