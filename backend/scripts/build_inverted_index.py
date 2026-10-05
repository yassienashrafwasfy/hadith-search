"""Build the BM25 index (terms, postings, lengths) in PostgreSQL."""

import time
from collections import Counter

import pandas as pd
from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from database import get_sync_session, init_schema_sync, read_hadiths_df
from models import HadithLength, Posting, Term

LANGUAGES = {"EN": "Preprocessed_English_Matn", "AR": "Preprocessed_Arabic_Matn"}
INSERT_BATCH = 20_000


def has_text(value):
    if pd.isna(value):
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in {"nan", "none", "null"}


def _matn_terms(row, language):
    column = LANGUAGES[language]
    if not has_text(row.get(column)):
        raise ValueError(
            f"Hadith id {row['id']} has empty {column}; refusing to build matn-only index"
        )
    return str(row[column]).strip().split()


def build_index_rows(df):
    """(lengths, terms, postings) row dicts for every hadith in `df`."""
    lengths, postings = [], []
    frequencies = {language: Counter() for language in LANGUAGES}  # term -> document frequency
    for _, row in df.iterrows():
        hadith_id = int(row["id"])
        terms = {language: _matn_terms(row, language) for language in LANGUAGES}
        lengths.append(
            {
                "hadith_id": hadith_id,
                "english_len": len(terms["EN"]),
                "arabic_len": len(terms["AR"]),
            }
        )
        for language, tokens in terms.items():
            for term, tf in Counter(tokens).items():
                postings.append(
                    {"language": language, "term": term, "hadith_id": hadith_id, "tf": tf}
                )
                frequencies[language][term] += 1
    term_rows = [
        {"language": language, "term": term, "df": df_count}
        for language, counter in frequencies.items()
        for term, df_count in counter.items()
    ]
    return lengths, term_rows, postings


def _insert_in_batches(session: Session, model, rows):
    for start in range(0, len(rows), INSERT_BATCH):
        session.execute(insert(model), rows[start : start + INSERT_BATCH])


def write_index(session: Session, df, commit: bool = True) -> tuple[int, int]:
    """Replace the stored index with one built from `df`; returns (English, Arabic) term counts.

    Delete and insert are one transaction: an error leaves the old index untouched. Pass
    `commit=False` to make it part of a larger transaction the caller commits.
    """
    lengths, terms, postings = build_index_rows(df)
    for model in (Posting, Term, HadithLength):
        session.execute(delete(model))
    _insert_in_batches(session, HadithLength, lengths)
    _insert_in_batches(session, Term, terms)
    _insert_in_batches(session, Posting, postings)
    if commit:
        session.commit()
    return tuple(sum(1 for t in terms if t["language"] == language) for language in LANGUAGES)


def run():
    init_schema_sync()
    df = read_hadiths_df()

    print(f"Loaded {len(df)} hadiths")
    print("Indexing Preprocessed_English_Matn / Preprocessed_Arabic_Matn only...")

    start = time.perf_counter()
    with get_sync_session() as session:
        en_terms, ar_terms = write_index(session, df)
    elapsed = time.perf_counter() - start

    print(f"English inverted index: {en_terms} unique terms")
    print(f"Arabic inverted index: {ar_terms} unique terms")
    print(f"Inverted index building took {elapsed:.3f}s")


if __name__ == "__main__":
    run()
