"""Per-release embedding tables.

Blue and green share one database, so the vectors of a new model cannot replace the live ones.
`hadith_embeddings_<release>` holds a full copy of the Arabic vectors for one release; the app
reads it when `EMBEDDINGS_RELEASE` names it and reads `hadith_embeddings` when it does not.
These tables are not in `Base.metadata`, so `init_schema` never creates an empty one by accident.
"""

from functools import lru_cache

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table

from models.orm import Hadith
from settings import check_release

PREFIX = "hadith_embeddings_"
_metadata = MetaData()


def table_name(release: str) -> str:
    return PREFIX + check_release(release)


@lru_cache(maxsize=None)
def embedding_table(release: str) -> Table:
    """The table object for a release (not created here; see `scripts/promote_model.py`)."""
    return Table(
        table_name(release),
        _metadata,
        Column(
            "hadith_id",
            Integer,
            ForeignKey(Hadith.__table__.c.id, ondelete="CASCADE"),
            primary_key=True,
            autoincrement=False,
        ),
        Column("arabic", VECTOR(), nullable=True),
    )
