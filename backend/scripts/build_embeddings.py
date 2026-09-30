import os
import sys

import pandas as pd
import torch
from sentence_transformers import SentenceTransformer

from database import get_sync_session, init_schema_sync, read_hadiths_df
from models import Hadith

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Reuse the exact query-side Arabic normalization so passages and queries
# land in the same token space. Do NOT reimplement these here.
from camel_tools.utils.dediac import dediac_ar

from scripts import normalize_arabic_text
from scripts.embedding_store import store_embeddings


def has_text(value):
    if pd.isna(value):
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in {"nan", "none", "null"}


def clean_text(value):
    return str(value).strip() if has_text(value) else ""


def normalize_arabic_passage(text):
    # Mirrors search.py query path: normalize_arabic_text(dediac_ar(query))
    return normalize_arabic_text(dediac_ar(text))


EMBEDDING_BATCH_SIZE = 32
MODEL_NAME = "intfloat/multilingual-e5-large"
MAX_REPORTED_IDS = 20


def _choose_device():
    """'cuda' when available, 'cpu' if the user confirms, None to abort."""
    if torch.cuda.is_available():
        print(f"Using device: cuda ({torch.cuda.get_device_name(0)})")
        return "cuda"
    print("CUDA is not available.")
    choice = input("Proceed with CPU (extremely slow)? [Y/N]: ").strip().lower()
    if choice != "y":
        print("Aborted.")
        return None
    print("Using CPU (this will be very slow)")
    return "cpu"


def _load_corpus():
    df = read_hadiths_df(
        Hadith.id,
        Hadith.English_Text,
        Hadith.Arabic_Text,
        Hadith.Chapter_Title_English,
        Hadith.Chapter_Title_Arabic,
        Hadith.English_Matn,
        Hadith.Arabic_Matn,
    )
    print(f"Loaded {len(df)} hadiths")
    missing = {
        language: df.loc[~df[f"{language}_Matn"].map(has_text), "id"].astype(int).tolist()
        for language in ("English", "Arabic")
    }
    if missing["English"] or missing["Arabic"]:
        raise ValueError(
            "Canonical bilingual-matn corpus contains missing matn: "
            f"English ids={missing['English'][:MAX_REPORTED_IDS]} "
            f"Arabic ids={missing['Arabic'][:MAX_REPORTED_IDS]}"
        )
    return df


def _passages(df, language, prepare):
    """`passage: ...` strings from the raw matn; `prepare` maps a matn to the embedded text."""
    column = f"{language}_Matn"
    texts = []
    for hadith_id, value in zip(df["id"], df[column]):
        matn = clean_text(value)
        if not matn:
            raise ValueError(
                f"Hadith id {hadith_id} is missing {column} in the canonical bilingual-matn corpus"
            )
        texts.append(f"passage: {prepare(matn)}")
    return texts


def _encode_and_save(model, texts, language, hadith_ids):
    """Encode `texts` and upsert the vectors into PostgreSQL; `language` is "EN" or "AR"."""
    embeddings = model.encode(texts, batch_size=EMBEDDING_BATCH_SIZE, show_progress_bar=True)
    if len(embeddings) != len(hadith_ids):
        raise ValueError(
            f"{language} embedding count mismatch: {len(embeddings)} embeddings "
            f"for {len(hadith_ids)} rows"
        )
    with get_sync_session() as session:
        store_embeddings(session, hadith_ids, embeddings, language)
    print(f"{language} embeddings saved")
    return embeddings


def run():
    device = _choose_device()
    if device is None:
        return
    init_schema_sync()
    df = _load_corpus()
    ids = df["id"].astype(int).tolist()
    model = SentenceTransformer(MODEL_NAME, device=device)

    # English: raw matn only (no chapter). E5 expects natural text; the query
    # path uses `query: {query}` with no preprocessing, so passages stay raw too.
    print("Generating English embeddings (passage: [Matn])...")
    _encode_and_save(model, _passages(df, "English", str), "EN", ids)

    # Arabic: raw matn only (no chapter), normalized to mirror the query path
    # in search.py: `query: {normalize_arabic_text(dediac_ar(query))}`.
    print("Generating Arabic embeddings (passage: [normalized Matn])...")
    ar_texts = _passages(df, "Arabic", normalize_arabic_passage)
    _encode_and_save(model, ar_texts, "AR", ids)

    print("Done")


if __name__ == "__main__":
    run()
