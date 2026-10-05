"""Exact-search text (services/exact_text.py): query words, the stored text, counting."""

from sqlalchemy import literal, select

import database
from models import Hadith, HadithExactText
from services import exact_text, ranking


def _ids(session, query, lang="EN"):
    return list(ranking.exact_search(session, query, lang))


def test_words_are_lowercased_and_deduplicated():
    assert exact_text.words("Prayer  PRAYER Zakat", "EN") == ["prayer", "zakat"]


def test_arabic_words_lose_marks_and_tatweel():
    assert exact_text.words("الصَّلاةُ  عمـاد", "AR") == ["الصلاة", "عماد"]


def test_english_words_are_not_stemmed():
    assert exact_text.words("praying prayers", "EN") == ["praying", "prayers"]


def test_words_are_capped():
    assert len(exact_text.words(" ".join(f"w{i}" for i in range(50)), "EN")) == 10


def test_words_split_on_anything_that_is_not_a_letter_or_digit():
    assert exact_text.words("100%, (yes) allah's pr.yer", "EN") == [
        "100",
        "yes",
        "allah",
        "s",
        "pr",
        "yer",
    ]
    assert exact_text.words("... %%", "EN") == []


def test_needles_have_a_space_on_both_sides():
    assert exact_text.needles("Prayer fasting", "EN") == [" prayer ", " fasting "]


async def test_rebuild_replaces_the_stored_text(_search_index, _db_session):
    assert exact_text.rebuild(_db_session) == 13  # the 3 hadiths and the 10 fillers
    _db_session.commit()
    assert _ids(_db_session, "shield") == [2]


async def test_rebuild_normalises_both_languages(_search_index, _db_session):
    with database.get_sync_session() as session:
        session.add(
            Hadith(
                id=70, Book="Muslim", English_Text="Hello, WORLD!", Arabic_Text="الصَّلاةُ، عمـاد"
            )
        )
        session.flush()
        assert exact_text.rebuild(session) == 14
        row = session.get(HadithExactText, 70)
        assert row.english == " hello world "
        assert row.arabic == " الصلاة عماد "
        session.commit()


async def test_a_hadith_without_text_gets_empty_text(_search_index, _db_session):
    row = _db_session.get(HadithExactText, 100)  # a filler hadith with no text
    assert row.english == "  " and row.arabic == "  "


def test_occurrences_counts_neighbours_and_ignores_longer_words(_db_session):
    def count(text, word):
        needle = exact_text.needles(word, "EN")[0]
        return _db_session.scalar(select(exact_text.occurrences(literal(f" {text} "), needle)))

    assert count("a a a b", "a") == 3
    assert count("zakat zakat", "zakat") == 2
    assert count("zakats azakat", "zakat") == 0
