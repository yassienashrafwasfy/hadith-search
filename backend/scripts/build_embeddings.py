import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import pandas as pd

from database import get_sync_session, init_schema_sync, read_hadiths_df
from models import Hadith
from scripts.arabic_encoder import encoding_text, load_encoder
from scripts.embedding_store import store_embeddings

MAX_REPORTED_IDS = 20


def has_text(value):
    if pd.isna(value):
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in {"nan", "none", "null"}


def clean_text(value):
    return str(value).strip() if has_text(value) else ""


def _load_corpus():
    df = read_hadiths_df(Hadith.id, Hadith.Arabic_Matn)
    print(f"Loaded {len(df)} hadiths")
    missing = df.loc[~df["Arabic_Matn"].map(has_text), "id"].astype(int).tolist()
    if missing:
        raise ValueError(
            f"Canonical corpus contains missing Arabic matn: ids={missing[:MAX_REPORTED_IDS]}"
        )
    return df


def _passages(df):
    """The Arabic matn of each hadith, cleaned the same way queries are (no prefix)."""
    texts = []
    for hadith_id, value in zip(df["id"], df["Arabic_Matn"]):
        matn = clean_text(value)
        if not matn:
            raise ValueError(f"Hadith id {hadith_id} is missing Arabic_Matn")
        texts.append(encoding_text(matn))
    return texts


def _encode_and_save(model, texts, hadith_ids):
    """Encode `texts` and upsert the vectors into PostgreSQL."""
    embeddings = model.encode(texts)
    if len(embeddings) != len(hadith_ids):
        raise ValueError(
            f"AR embedding count mismatch: {len(embeddings)} embeddings for {len(hadith_ids)} rows"
        )
    with get_sync_session() as session:
        store_embeddings(session, hadith_ids, embeddings)
    print("AR embeddings saved")
    return embeddings


def run():
    init_schema_sync()
    df = _load_corpus()
    ids = df["id"].astype(int).tolist()
    model = load_encoder(threads=0)  # a batch job: use every core
    print("Generating Arabic embeddings (ONNX, matn only)...")
    _encode_and_save(model, _passages(df), ids)
    print("Done")


if __name__ == "__main__":
    run()
