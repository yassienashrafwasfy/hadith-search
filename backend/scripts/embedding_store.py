"""Write E5 embeddings into the `hadith_embeddings` table (one vector column per language)."""

import numpy as np
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from models import HadithEmbedding

COLUMNS = {"EN": "english", "AR": "arabic"}
BATCH = 1000


def store_embeddings(session: Session, hadith_ids, embeddings, language: str) -> int:
    """Upsert one vector per hadith as float32; the other language's vector is left alone."""
    column = COLUMNS[language]
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
