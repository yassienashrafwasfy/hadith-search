"""Text for exact keyword search: what is stored, how a query is cut into words, how to count.

A hadith's exact text is its English or Arabic text, lowercased, with Arabic marks (tatweel,
harakat, dagger alef) removed and every run of characters that are not letters or digits turned
into one space, padded with one space at each end. A word is whole when ` word ` is a substring,
which a pg_trgm GIN index serves and a plain LIKE can check. Nothing else is normalised: no
stemming, no letter variants, so `أ` and `ا` stay different.
"""

import re

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from models import Hadith, HadithExactText

MAX_WORDS = 10  # more query words than this are ignored (bounds the work per request)

# Tatweel, the harakat U+064B..U+065F and dagger alef.
ARABIC_MARKS = "".join(map(chr, [0x640, *range(0x64B, 0x660), 0x670]))
_MARKS = re.compile(f"[{ARABIC_MARKS}]")
# What separates words: the same class in PostgreSQL (build) and Python (query).
_SQL_SEPARATOR = {"EN": "[^[:alnum:]]+", "AR": "[^ء-ۓa-z0-9]+"}
_PY_SEPARATOR = {"EN": re.compile(r"[\W_]+"), "AR": re.compile("[^ء-ۓa-z0-9]+")}


def words(query: str, lang: str) -> list[str]:
    """The query words as typed: lowercased, Arabic marks removed, no stemming, no repeats."""
    text = query.lower()
    if lang == "AR":
        text = _MARKS.sub("", text)
    found = (word for word in _PY_SEPARATOR[lang].split(text) if word)
    return list(dict.fromkeys(found))[:MAX_WORDS]


def needles(query: str, lang: str) -> list[str]:
    """The strings to look for in the stored text: each word with a space on both sides."""
    return [f" {word} " for word in words(query, lang)]


def column(lang: str):
    return HadithExactText.arabic if lang == "AR" else HadithExactText.english


def occurrences(text_column, needle: str):
    """SQL: how many times `needle` occurs in the column.

    Spaces are doubled first so two neighbouring occurrences (`a a`) do not share the one space
    between them; each match then removes `len(needle)` characters.
    """
    doubled = func.replace(text_column, " ", "  ")
    return (func.length(doubled) - func.length(func.replace(doubled, needle, ""))) / len(needle)


def _normalised(source, lang: str):
    text = func.lower(source)
    if lang == "AR":
        text = func.translate(text, ARABIC_MARKS, "")
    spaced = func.regexp_replace(text, _SQL_SEPARATOR[lang], " ", "g")
    return func.concat(" ", func.trim(spaced), " ")


def rebuild(session: Session) -> int:
    """Rebuild the exact text of every hadith inside the caller's transaction; returns the count."""
    session.execute(delete(HadithExactText))
    texts = select(
        Hadith.id, _normalised(Hadith.English_Text, "EN"), _normalised(Hadith.Arabic_Text, "AR")
    )
    session.execute(insert(HadithExactText).from_select(["hadith_id", "english", "arabic"], texts))
    return session.scalar(select(func.count()).select_from(HadithExactText))
