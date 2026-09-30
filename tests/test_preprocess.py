"""Preprocessing tests. Pure-string helpers run anywhere; anything needing NLTK
corpora or CAMeL data is marked `nlp` and skipped when that data is missing."""

from types import SimpleNamespace

import pytest

from scripts import preprocess as p


def test_normalize_arabic_text_unifies_letters():
    out = p.normalize_arabic_text("الله أكبر!!  English 123")
    assert "!" not in out and "English" not in out and "  " not in out
    assert "أ" not in out and "ا" in out  # hamza on alef normalised away


def test_normalize_arabic_strips_tatweel():
    assert p.normalize_arabic_text("الـــصلاة") == p.normalize_arabic_text("الصلاة")


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, False),
        ("", False),
        ("  ", False),
        ("nan", False),
        ("None", False),
        ("null", False),
        ("text", True),
        (5, True),
    ],
)
def test_has_text(value, expected):
    assert p.has_text(value) is expected


@pytest.mark.parametrize(
    "tag,pos", [("JJ", "a"), ("VBD", "v"), ("RB", "r"), ("NN", "n"), ("XX", "n")]
)
def test_get_wordnet_pos(monkeypatch, tag, pos):
    # avoid loading the real WordNet corpus: only the POS constants are needed
    monkeypatch.setattr(p, "wordnet", SimpleNamespace(ADJ="a", VERB="v", ADV="r", NOUN="n"))
    assert p.get_wordnet_pos(tag) == pos


def test_remove_stopwords_english_filters_short_and_stop(monkeypatch):
    monkeypatch.setattr(p, "_stop_words_en", {"the", "hadith"})
    assert p.remove_stopwords_english(["the", "hadith", "of", "prayer"]) == ["prayer"]


def test_process_arabic_tokens_mocked_disambiguation(monkeypatch):
    """CAMeL analyses are faked: verifies POS filtering, fallback and stopword removal."""
    monkeypatch.setattr(p, "_stop_words_ar", set())

    class A:
        def __init__(self, lex, pos):
            self.analysis = {"lex": lex, "pos": pos}

    class W:
        def __init__(self, analyses):
            self.analyses = analyses

    words = [W([A("صلاة", "noun")]), W([A("في", "prep")]), W([])]
    assert p.process_arabic_tokens(words, ["الصلاة", "في", "زكاة"]) == ["صلاة", "زكاة"]


def _nltk_ready():
    try:
        p.preprocess_english("test")
        return True
    except Exception:
        return False


@pytest.mark.nlp
def test_preprocess_english_real():
    if not _nltk_ready():
        pytest.skip("NLTK data not installed")
    assert (
        p.preprocess_english("The Prophet said: prayers are performed") == "prophet prayer perform"
    )
