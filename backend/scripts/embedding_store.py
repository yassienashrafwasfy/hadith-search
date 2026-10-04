"""Write the Arabic sentence embeddings into `hadith_embeddings` (or a release table)."""

import numpy as np
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from models import HadithEmbedding
from models.embedding_sets import embedding_table

BATCH = 1000


def store_embeddings(session: Session, hadith_ids, embeddings, release: str | None = None) -> int:
    """Upsert one float32 vector per hadith into the `arabic` column.

    `release` names a `hadith_embeddings_<release>` table that already exists; without it the
    vectors go to `hadith_embeddings`.
    """
    table = HadithEmbedding.__table__ if release is None else embedding_table(release)
    column = "arabic"
    matrix = np.asarray(embeddings, dtype=np.float32)
    if len(matrix) != len(hadith_ids):
        raise ValueError(f"{len(matrix)} embeddings for {len(hadith_ids)} hadiths")
    for start in range(0, len(matrix), BATCH):
        rows = [
            {"hadith_id": int(hadith_id), column: vector.tolist()}
            for hadith_id, vector in zip(
                hadith_ids[start : start + BATCH], matrix[start : start + BATCH]
            )
        ]
        stmt = pg_insert(table).values(rows)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["hadith_id"], set_={column: stmt.excluded[column]}
            )
        )
    session.commit()
    return len(matrix)
