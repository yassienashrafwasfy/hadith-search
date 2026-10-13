import pytest

from quran.retrieval import auto_merge, get_passage, search_source
from quran.types import Filters, Level, Place


def _q(encoder, ayah):
    return encoder.encode([ayah])[0]


def test_search_finds_exact_ayah_first(_quran_session, _fake_encoder, _mini_corpus):
    target = next(a for a in _mini_corpus if (a.surah, a.ayah) == (112, 2))
    from quran_support import _simple

    hits = search_source(
        _quran_session, "quran", _q(_fake_encoder, _simple(target.text)), Filters(), 3
    )
    assert (hits[0].surah, hits[0].ayah_from) == (112, 2)
    assert hits[0].text == target.text and hits[0].level is Level.AYAH
    assert hits[0].score == pytest.approx(1.0)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_search_returns_only_ayah_leaves(_quran_session, _fake_encoder):
    hits = search_source(_quran_session, "quran", _q(_fake_encoder, "x"), Filters(), 100)
    assert len(hits) == 17 and {h.level for h in hits} == {Level.AYAH}


def test_filters_apply_before_top_k(_quran_session, _fake_encoder):
    vec = _q(_fake_encoder, "anything")
    hits = search_source(_quran_session, "quran", vec, Filters(surah=114), 2)
    assert len(hits) == 2 and {h.surah for h in hits} == {114}
    hits = search_source(_quran_session, "quran", vec, Filters(surah=1, ayah_from=3, ayah_to=4), 10)
    assert sorted(h.ayah_from for h in hits) == [3, 4]
    assert len(search_source(_quran_session, "quran", vec, Filters(juz=30), 100)) == 10
    assert search_source(_quran_session, "quran", vec, Filters(place=Place.MEDINAN), 5) == []
    assert len(search_source(_quran_session, "quran", vec, Filters(place=Place.MECCAN), 100)) == 17


def test_author_filter_and_empty_k(_quran_session, _fake_encoder):
    vec = _q(_fake_encoder, "t")
    assert (
        len(search_source(_quran_session, "tafsir_mini", vec, Filters(author="muyassar"), 9)) == 4
    )
    assert search_source(_quran_session, "tafsir_mini", vec, Filters(author="other"), 9) == []
    assert search_source(_quran_session, "quran", vec, Filters(), 0) == []


def _hits(session, source, surah, ayat):
    return [h for h in get_passage(session, source, surah, 1, 99) if h.ayah_from in ayat]


def test_auto_merge_replaces_children_with_original_parent_text(_quran_session, _mini_corpus):
    hits = _hits(_quran_session, "quran", 112, {1, 2, 3})  # 3 of 4 children
    merged = auto_merge(_quran_session, hits, 0.5)
    assert len(merged) == 1 and merged[0].level is Level.PASSAGE
    joined = " ".join(a.text for a in _mini_corpus if a.surah == 112)
    assert merged[0].text == joined and merged[0].text != "ملخص"
    assert (merged[0].surah, merged[0].ayah_from, merged[0].ayah_to) == (112, 1, 4)


def test_auto_merge_below_fraction_keeps_leaves(_quran_session):
    hits = _hits(_quran_session, "quran", 112, {1})  # 1 of 4
    assert auto_merge(_quran_session, hits, 0.5) == hits


def test_auto_merge_at_exact_fraction_and_order(_quran_session):
    other = _hits(_quran_session, "quran", 1, {2})
    hits = other + _hits(_quran_session, "quran", 112, {4, 1})  # 2 of 4 = 0.5
    merged = auto_merge(_quran_session, hits, 0.5)
    assert [m.level for m in merged] == [Level.AYAH, Level.PASSAGE]
    assert merged[0] == other[0]


def test_auto_merge_score_and_duplicates(_quran_session):
    hits = _hits(_quran_session, "quran", 112, {1, 2})
    scored = [h.__class__(**{**h.__dict__, "score": s}) for h, s in zip(hits, (0.2, 0.9))]
    merged = auto_merge(_quran_session, scored + scored, 0.5)
    assert len(merged) == 1 and merged[0].score == 0.9


def test_auto_merge_rejects_bad_fraction(_quran_session):
    for bad in (0, 1.5, -1):
        with pytest.raises(ValueError):
            auto_merge(_quran_session, [], bad)
    assert auto_merge(_quran_session, [], 0.5) == []


def test_get_passage_range_and_order(_quran_session, _mini_corpus):
    got = get_passage(_quran_session, "quran", 1, 3, 5)
    assert [g.ayah_from for g in got] == [3, 4, 5]
    assert got[0].text == next(a.text for a in _mini_corpus if (a.surah, a.ayah) == (1, 3))
    assert [g.ayah_from for g in get_passage(_quran_session, "quran", 1, 5, 3)] == [3, 4, 5]
    assert get_passage(_quran_session, "quran", 2, 1, 5) == []


def test_keyword_tokens_drop_question_words_and_the_article():
    from quran.retrieval import keyword_tokens

    assert keyword_tokens("ما هي آية الكرسي", encode_text=lambda t: t) == ["كرسي"]
    assert keyword_tokens("في", encode_text=lambda t: t) == []
    assert keyword_tokens("آية الكرسي؟", encode_text=lambda t: t) == ["كرسي"]
    assert keyword_tokens("ماذا قال ابن كثير في تفسير الصبر", encode_text=lambda t: t) == ["صبر"]


def test_a_keyword_hit_is_fused_in_even_when_the_vector_misses(
    _quran_session, _fake_encoder, _mini_corpus
):
    from quran_support import _simple

    texts = {(a.surah, a.ayah): _simple(a.text).split() for a in _mini_corpus}

    def unique(key):
        rest = {w for k, ws in texts.items() if k != key for w in ws}
        return [w for w in texts[key] if len(w) >= 3 and w not in rest]

    key, word = next((k, unique(k)[0]) for k in texts if unique(k))
    wrong_vector = _q(_fake_encoder, "unrelated words entirely")

    hits = search_source(_quran_session, "quran", wrong_vector, Filters(), 3, word)

    assert key in {(h.surah, h.ayah_from) for h in hits}


def test_a_question_without_a_distinctive_word_finds_nothing(_quran_session, _fake_encoder):
    vec = _q(_fake_encoder, "anything")
    assert search_source(_quran_session, "quran", vec, Filters(), 5, "كيف أطبخ الأرز؟") == []
    assert search_source(_quran_session, "quran", vec, Filters(), 5, "في") == []
    assert len(search_source(_quran_session, "quran", vec, Filters(), 5)) == 5  # no question text
