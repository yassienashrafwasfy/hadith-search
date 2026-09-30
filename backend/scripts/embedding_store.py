"""Write the Arabic sentence embeddings into the `hadith_embeddings` table."""

import numpy as np
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from models import HadithEmbedding

BATCH = 1000


def store_embeddings(session: Session, hadith_ids, embeddings) -> int:
    """Upsert one float32 vector per hadith into the `arabic` column."""
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
        stmt = pg_insert(HadithEmbedding).values(rows)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["hadith_id"], set_={column: stmt.excluded[column]}
            )
        )
    session.commit()
    return len(matrix)
