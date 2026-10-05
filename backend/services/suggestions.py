"""Autocomplete and typo hints from the pg_trgm indexes on `terms` and `chapters`.

The vocabulary is the indexed terms (lemmas; Arabic in its normalised spelling) plus chapter
titles, so a suggestion is a word the BM25 index knows, not always a surface form of the corpus.

Thresholds (trigram similarity, 0 to 1):
- `SUGGEST_SIMILARITY` 0.3 (pg_trgm's default). A suggestion is a prefix match or at least this
  similar. It is set as `pg_trgm.similarity_threshold` for the transaction, so the `%` operator
  (which the GIN index serves) does not depend on the server setting.
- `HINT_SIMILARITY` 0.4, stricter: a typo is only corrected to a term at least this similar,
  so an unrelated word is never offered.
"""

import re

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from models import Chapter, Term
from scripts.preprocess import normalize_arabic_text

SUGGEST_SIMILARITY = 0.3
HINT_SIMILARITY = 0.4
MAX_SUGGESTIONS = 10
HINT_MAX_WORDS = 10

_ARABIC = re.compile(r"[؀-ۿ]")
_NOT_LETTERS = re.compile(r"[\W\d_]+")
# Tatweel, the harakat U+064B..U+065F and dagger alef.
ARABIC_MARKS = "".join(map(chr, [0x640, *range(0x64B, 0x660), 0x670]))
_MARKS = re.compile(f"[{ARABIC_MARKS}]")


def fold(text: str) -> str:
    """The form the terms table uses: lowercase letters, or normalised Arabic."""
    text = text.strip().lower()
    if _ARABIC.search(text):
        return normalize_arabic_text(_MARKS.sub("", text))
    return _NOT_LETTERS.sub(" ", text).strip()


def _threshold(session: Session, value: float) -> None:
    """Set the `%` operator's similarity threshold for this transaction (the default is 0.3)."""
    session.execute(select(func.set_config("pg_trgm.similarity_threshold", str(value), True)))


def _similar(column, text: str):
    """pg_trgm `%`: true above the similarity threshold (default 0.3); uses the GIN index."""
    return column.op("%")(text)


def _term_matches(session: Session, text: str, limit: int) -> list[dict]:
    prefix = Term.term.startswith(text, autoescape=True)
    similarity = func.similarity(Term.term, text)
    stmt = (
        select(Term.term, Term.language, prefix, similarity)
        .where(or_(prefix, _similar(Term.term, text)))
        .order_by(prefix.desc(), similarity.desc(), Term.df.desc(), Term.term)
        .limit(limit)
    )
    return [
        {"text": term, "kind": "term", "lang": language.lower(), "rank": (int(starts), sim)}
        for term, language, starts, sim in session.execute(stmt)
    ]


def _chapter_matches(session: Session, text: str, limit: int) -> list[dict]:
    found = []
    for column, lang in ((Chapter.title_english, "en"), (Chapter.title_arabic, "ar")):
        similarity = func.max(func.similarity(column, text))
        stmt = (
            select(column, similarity)
            .where(or_(column.icontains(text, autoescape=True), _similar(column, text)))
            .group_by(column)
            .order_by(similarity.desc(), column)
            .limit(limit)
        )
        found += [
            {
                "text": title,
                "kind": "chapter",
                "lang": lang,
                "rank": (int(title.lower().startswith(text)), sim),
            }
            for title, sim in session.execute(stmt)
        ]
    return found


def suggest(session: Session, query: str, limit: int = MAX_SUGGESTIONS) -> list[dict]:
    """Vocabulary words and chapter titles for a partial query, best first, at most `limit`."""
    text = fold(query)
    if not text:
        return []
    _threshold(session, SUGGEST_SIMILARITY)
    found = _term_matches(session, text, limit) + _chapter_matches(session, text, limit)
    found.sort(
        key=lambda item: (-item["rank"][0], -item["rank"][1], item["kind"] != "term", item["text"])
    )
    return [{key: item[key] for key in ("text", "kind", "lang")} for item in found[:limit]]


def _closest_term(session: Session, word: str, lang: str) -> str | None:
    _threshold(session, HINT_SIMILARITY)
    similarity = func.similarity(Term.term, word)
    stmt = (
        select(Term.term)
        .where(Term.language == lang, _similar(Term.term, word))
        .order_by(similarity.desc(), Term.df.desc(), Term.term)
        .limit(1)
    )
    return session.scalar(stmt)


def did_you_mean(session: Session, query: str, lang: str) -> str | None:
    """The query with each unknown word replaced by its closest known term, or None.

    A word is unknown when no indexed term equals it. Only called after a keyword search found
    nothing, so the happy path never pays for it.
    """
    words = query.split()[:HINT_MAX_WORDS]
    folded = [fold(word) for word in words]
    known = set(
        session.scalars(select(Term.term).where(Term.language == lang, Term.term.in_(folded)))
    )
    fixed, changed = [], False
    for word, plain in zip(words, folded):
        best = None if not plain or plain in known else _closest_term(session, plain, lang)
        changed = changed or best is not None
        fixed.append(best or word)
    return " ".join(fixed) if changed else None
