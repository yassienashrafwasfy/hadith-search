import os
import re

import pyarabic.araby as araby
from camel_tools.tokenizers.word import simple_word_tokenize
from camel_tools.utils.dediac import dediac_ar
from camel_tools.utils.normalize import (
    normalize_alef_ar,
    normalize_alef_maksura_ar,
    normalize_teh_marbuta_ar,
)
from nltk import pos_tag
from nltk.corpus import stopwords, wordnet
from nltk.tokenize import word_tokenize

from scripts.loading import get_english_lemmatizer, get_mle

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def get_wordnet_pos(tag):  # this is needed to convert nltk pos to wordnet pos
    if tag.startswith("J"):
        return wordnet.ADJ
    elif tag.startswith("V"):
        return wordnet.VERB
    elif tag.startswith("R"):
        return wordnet.ADV
    else:
        return wordnet.NOUN


def lemmatize_english(tokens, lemmatizer):
    pos_tags = pos_tag(tokens)
    lemmatized_tokens = [lemmatizer.lemmatize(word, get_wordnet_pos(tag)) for word, tag in pos_tags]
    return lemmatized_tokens


_stop_words_en = None


def get_stop_words_english():
    global _stop_words_en
    if _stop_words_en is None:
        extra = {
            "say",
            "narrate",
            "told",
            "informed",
            "reported",
            "transmitted",
            "heard",
            "narration",
            "authority",
            "correct",
            "weak",
            "bin",
            "ibn",
            "abu",
            "abi",
            "hadith",
        }
        _stop_words_en = set(stopwords.words("english")) | extra
    return _stop_words_en


def remove_stopwords_english(tokens: list[str]) -> list[str]:
    stop_words = get_stop_words_english()
    return [word for word in tokens if word not in stop_words and len(word) >= 3]


def preprocess_english(text):
    lemmatizer = get_english_lemmatizer()
    text = re.sub(r"[^a-zA-Z\s]", "", text.lower())
    tokens = word_tokenize(text)
    lemmatized_tokens = lemmatize_english(tokens, lemmatizer)
    final_tokens = remove_stopwords_english(lemmatized_tokens)
    return " ".join(final_tokens)


def has_text(value):
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in {"nan", "none", "null"}


# Arabic pipeline
def normalize_arabic_stopwords(stopwords: set) -> set:
    result = set()
    for word in stopwords:
        word = dediac_ar(word)
        word = normalize_alef_ar(word)
        word = normalize_alef_maksura_ar(word)
        word = araby.normalize_hamza(word, method="tasheel")
        result.add(word)
    return result


_stop_words_ar = None


def get_stop_words_arabic() -> set:
    global _stop_words_ar
    if _stop_words_ar is None:
        raw = {
            "قال",
            "حدث",
            "روى",
            "حديث",
            "ضعيف",
            "قوى",
            "صلى",
            "سلم",
            "بن",
            "ابن",
            "ابي",
            "ابو",
            "اخبر",
        }
        _stop_words_ar = normalize_arabic_stopwords(raw)
    return _stop_words_ar


def normalize_token(token):
    token = dediac_ar(token)
    token = normalize_alef_ar(token)
    token = normalize_alef_maksura_ar(token)
    token = araby.normalize_hamza(token, method="tasheel")
    return token


def process_arabic_tokens(disambiguated_tokens, original_tokens):
    extra_stopwords = get_stop_words_arabic()
    stop_pos = {"prep", "conj", "part", "punc"}
    # remove prepositions conjunctions particles and punctuations, keep pronouns for now
    final_tokens = []

    for i, word in enumerate(disambiguated_tokens):
        if not word.analyses:
            raw = dediac_ar(original_tokens[i])
            if raw and len(raw) >= 2 and raw not in extra_stopwords:
                final_tokens.append(normalize_token(raw))
            continue

        best_analysis = word.analyses[0]
        lemma = best_analysis.analysis["lex"]
        pos = best_analysis.analysis["pos"]
        clean_lemma = normalize_token(lemma)
        if pos not in stop_pos and clean_lemma not in extra_stopwords and len(clean_lemma) >= 2:
            final_tokens.append(clean_lemma)

    return final_tokens


def normalize_arabic_text(text):
    text = re.sub(r"[^\u0600-\u06FF\s]", " ", text)
    text = normalize_alef_ar(text)
    text = normalize_alef_maksura_ar(text)
    text = normalize_teh_marbuta_ar(text)
    text = araby.normalize_hamza(text, method="tasheel")
    text = araby.strip_tatweel(text)  # tatweel is safe to remove, as it's only for looks
    text = re.sub(r"\s+", " ", text).strip()
    return text


def preprocess_arabic(text):
    text = normalize_arabic_text(text)
    tokens = simple_word_tokenize(text)
    if not tokens:
        return ""
    mle = get_mle()
    disambiguated = mle.disambiguate(tokens)
    processed = process_arabic_tokens(disambiguated, tokens)  # pass original tokens as fallback
    return " ".join(processed)


ARABIC_WORKERS = 4
DELETE_BATCH = 500
SAMPLE_IDS = 10

# (label, source column, Preprocessed_* column, language)
_COLUMNS = (
    ("full English text", "English_Text", "Preprocessed_English", "EN"),
    ("full Arabic text", "Arabic_Text", "Preprocessed_Arabic", "AR"),
    ("English isnad text", "English_Isnad", "Preprocessed_English_Isnad", "EN"),
    ("Arabic isnad text", "Arabic_Isnad", "Preprocessed_Arabic_Isnad", "AR"),
    ("English matn text", "English_Matn", "Preprocessed_English_Matn", "EN"),
    ("Arabic matn text", "Arabic_Matn", "Preprocessed_Arabic_Matn", "AR"),
)


def _preprocess_english_texts(texts):
    return [preprocess_english(t) if t else "" for t in texts]


def _preprocess_arabic_texts(texts):
    """Thread pool first; sequential fallback if the pool fails."""
    from concurrent.futures import ThreadPoolExecutor

    try:
        with ThreadPoolExecutor(max_workers=ARABIC_WORKERS) as ex:
            return list(ex.map(lambda t: preprocess_arabic(t) if t else "", texts)), "parallel"
    except Exception:
        print("  ThreadPoolExecutor failed, falling back to sequential...")
        return [preprocess_arabic(t) if t else "" for t in texts], "sequential"


def _preprocess_column(df, label, column, language):
    import time

    print(f"Preprocessing {label}...")
    started = time.perf_counter()
    texts = [t if isinstance(t, str) else "" for t in df[column].tolist()]  # NULL/NaN -> ""
    if language == "AR":
        results, mode = _preprocess_arabic_texts(texts)
        suffix = f" ({mode})"
    else:
        results, suffix = _preprocess_english_texts(texts), ""
    print(f"  Done in {time.perf_counter() - started:.2f}s{suffix}")
    return results


def _empty_ids(df, values):
    return {int(hid) for hid, value in zip(df["id"], values) if not has_text(value)}


def _load_drop_audit(path):
    import json

    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "reason": "Missing bilingual matn after deterministic reconstruction",
            "count": 0,
            "rows": [],
            "second_stage": None,
        }


def _drop_rows(df, drop_ids):
    return [
        {
            "id_before_drop": int(row.id),
            "LK_Book": getattr(row, "Book", ""),
            "Book": getattr(row, "Book", ""),
            "Hadith_Number": getattr(row, "Hadith_Number", ""),
            "Chapter_Number": getattr(row, "Chapter_Number", ""),
        }
        for row in df.itertuples()
        if int(row.id) in drop_ids
    ]


def _write_drop_audit(path, df, empty_en, empty_ar):
    import json

    drop_ids = empty_en | empty_ar
    audit = _load_drop_audit(path)
    audit["second_stage"] = {
        "reason": "Empty preprocessed matn in one or both languages (isnad-only alternate chains / cross-references)",
        "count": len(drop_ids),
        "count_en": len(empty_en),
        "count_ar": len(empty_ar),
        "rows": _drop_rows(df, drop_ids),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2, ensure_ascii=False)


def _delete_hadiths(session, ids):
    from sqlalchemy import delete

    from models import Hadith

    for i in range(0, len(ids), DELETE_BATCH):
        session.execute(delete(Hadith).where(Hadith.id.in_(ids[i : i + DELETE_BATCH])))


def _report_drops(path, empty_en, empty_ar):
    print(
        f"\nDropped {len(empty_en | empty_ar)} hadiths with empty preprocessed matn "
        f"(EN={len(empty_en)}, AR={len(empty_ar)})"
    )
    print(f"  Appended to {path}")
    for name, ids in (("English", empty_en), ("Arabic", empty_ar)):
        if ids:
            print(f"  Sample {name}-empty IDs: {sorted(ids)[:SAMPLE_IDS]}")


def _drop_empty_matn(session, df, results, data_dir):
    """Delete rows whose preprocessed matn is empty in either language; returns their ids."""

    empty_en = _empty_ids(df, results["Preprocessed_English_Matn"])
    empty_ar = _empty_ids(df, results["Preprocessed_Arabic_Matn"])
    drop_ids = sorted(empty_en | empty_ar)
    if not drop_ids:
        return set()
    path = os.path.join(data_dir, "dropped_lk_rows.json")
    _write_drop_audit(path, df, empty_en, empty_ar)
    _delete_hadiths(session, drop_ids)
    _report_drops(path, empty_en, empty_ar)
    return set(drop_ids)


def _build_updates(df, results, dropped):
    return [
        {"id": int(hid), **{column: values[i] for column, values in results.items()}}
        for i, hid in enumerate(df["id"])
        if int(hid) not in dropped
    ]


def run():
    import time

    from sqlalchemy import update

    from database import get_sync_session, init_schema_sync, read_hadiths_df
    from models import Hadith

    start = time.perf_counter()
    init_schema_sync()
    df = read_hadiths_df()

    results = {
        target: _preprocess_column(df, label, source, language)
        for label, source, target, language in _COLUMNS
    }
    with get_sync_session() as session:
        dropped = _drop_empty_matn(session, df, results, DATA_DIR)
        session.execute(update(Hadith), _build_updates(df, results, dropped))
        session.commit()

    print(f"\nPreprocessing Successful. Total time: {time.perf_counter() - start:.2f}s")


if __name__ == "__main__":
    run()
