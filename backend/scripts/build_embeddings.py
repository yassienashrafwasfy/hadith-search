import os
import sys
import torch
from sentence_transformers import SentenceTransformer
import numpy as np
import sqlite3
import pandas as pd

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Reuse the exact query-side Arabic normalization so passages and queries
# land in the same token space. Do NOT reimplement these here.
from scripts.preprocess import normalize_arabic_text
from camel_tools.utils.dediac import dediac_ar


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


def run():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DB_PATH = os.path.join(BASE_DIR, "..", "data", "hadiths.db")

    if not torch.cuda.is_available():
        print("CUDA is not available.")
        choice = input("Proceed with CPU (extremely slow)? [Y/N]: ").strip().lower()
        if choice != 'y':
            print("Aborted.")
            return
        device = 'cpu'
        print("Using CPU (this will be very slow)")
    else:
        device = 'cuda'
        print(f"Using device: {device} ({torch.cuda.get_device_name(0)})")

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT id, English_Text, Arabic_Text,
               Chapter_Title_English, Chapter_Title_Arabic,
               English_Matn, Arabic_Matn
        FROM hadiths
    """, conn)
    conn.close()

    print(f"Loaded {len(df)} hadiths")
    missing_english = df.loc[~df["English_Matn"].map(has_text), "id"].astype(int).tolist()
    missing_arabic = df.loc[~df["Arabic_Matn"].map(has_text), "id"].astype(int).tolist()
    if missing_english or missing_arabic:
        raise ValueError(
            "Canonical bilingual-matn corpus contains missing matn: "
            f"English ids={missing_english[:20]} Arabic ids={missing_arabic[:20]}"
        )

    model = SentenceTransformer("intfloat/multilingual-e5-large", device=device)

    # English: raw matn only (no chapter). E5 expects natural text; the query
    # path uses `query: {query}` with no preprocessing, so passages stay raw too.
    print("Generating English embeddings (passage: [Matn])...")
    en_texts = []
    for _, row in df.iterrows():
        matn = clean_text(row.get("English_Matn"))
        if not matn:
            raise ValueError(f"Hadith id {row['id']} is missing English_Matn in the canonical bilingual-matn corpus")
        en_texts.append(f"passage: {matn}")
    en_embeddings = model.encode(en_texts, batch_size=32, show_progress_bar=True)
    if len(en_embeddings) != len(df):
        raise ValueError(f"English embedding count mismatch: {len(en_embeddings)} embeddings for {len(df)} rows")
    np.save(os.path.join(BASE_DIR, "..", "data", "english_embeddings.npy"), en_embeddings)
    print("English embeddings saved")

    # Arabic: raw matn only (no chapter), normalized to mirror the query path
    # in search.py: `query: {normalize_arabic_text(dediac_ar(query))}`.
    print("Generating Arabic embeddings (passage: [normalized Matn])...")
    ar_texts = []
    for _, row in df.iterrows():
        matn = clean_text(row.get("Arabic_Matn"))
        if not matn:
            raise ValueError(f"Hadith id {row['id']} is missing Arabic_Matn in the canonical bilingual-matn corpus")
        ar_texts.append(f"passage: {normalize_arabic_passage(matn)}")
    ar_embeddings = model.encode(ar_texts, batch_size=32, show_progress_bar=True)
    if len(ar_embeddings) != len(df):
        raise ValueError(f"Arabic embedding count mismatch: {len(ar_embeddings)} embeddings for {len(df)} rows")
    np.save(os.path.join(BASE_DIR, "..", "data", "arabic_embeddings.npy"), ar_embeddings)
    print("Arabic embeddings saved")

    np.save(os.path.join(BASE_DIR, "..", "data", "hadith_ids.npy"), df["id"].values)
    if len(df["id"].values) != len(en_embeddings) or len(df["id"].values) != len(ar_embeddings):
        raise ValueError("hadith_ids length does not match embedding arrays")
    print("Done")


if __name__ == "__main__":
    run()
