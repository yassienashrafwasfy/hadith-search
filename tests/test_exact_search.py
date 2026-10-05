"""Exact keyword search and its fusion with the dense ranking (services/ranking.py)."""

import pytest

import database
from models import Hadith
from services import exact_text, ranking

SEARCH = "/api/v1/searches"


def _add(rows):
    with database.get_sync_session() as session:
        session.add_all(Hadith(**row) for row in rows)
        session.flush()
        exact_text.rebuild(session)
        session.commit()


def _ids(session, query, lang="EN"):
    return list(ranking.exact_search(session, query, lang))


# ---------- matching ----------


async def test_all_words_must_match(_search_index, _db_session):
    assert _ids(_db_session, "prayer") == [1, 3]
    assert _ids(_db_session, "prayer fasting") == [3]
    assert _ids(_db_session, "prayer shield") == []


async def test_matching_ignores_case(_search_index, _db_session):
    assert _ids(_db_session, "PRAYER Fasting") == [3]


async def test_no_stemming_and_whole_words_only(_search_index, _db_session):
    assert _ids(_db_session, "pray") == []  # "prayer" contains it, but it is a longer word
    assert _ids(_db_session, "prayers") == []
    assert _ids(_db_session, "fast") == []


async def test_arabic_ignores_diacritics_in_query_and_text(_search_index, _db_session):
    assert _ids(_db_session, "الصَّلاة", "AR") == [1, 3]
    _add([{"id": 50, "Arabic_Text": "الصَّلَاةُ عِمَادُ الدِّينِ", "Book": "Bukhari"}])
    assert _ids(_db_session, "الصلاة عماد", "AR") == [1, 50]


async def test_arabic_has_no_normalisation_beyond_marks(_search_index, _db_session):
    assert _ids(_db_session, "الاجر", "AR") == []  # the text has أجر with a hamza


async def test_ranked_by_occurrences_then_id(_search_index, _db_session):
    _add(
        [
            {"id": 20, "English_Text": "zakat zakat zakat", "Book": "Muslim"},
            {"id": 21, "English_Text": "zakat zakat", "Book": "Muslim"},
            {"id": 11, "English_Text": "zakat zakat", "Book": "Muslim"},
            {"id": 22, "English_Text": "zakat and zakat's reward", "Book": "Muslim"},
        ]
    )
    scores = ranking.exact_search(_db_session, "zakat", "EN")
    assert list(scores) == [20, 11, 21, 22]  # ties go to the lower id; "zakat's" counts
    assert scores[20] == 3 and scores[22] == 2


async def test_limit_keeps_the_best(_search_index, _db_session):
    assert list(ranking.exact_search(_db_session, "prayer", "EN", limit=1)) == [1]


@pytest.mark.parametrize("query", ["%", "_", "%%", "pr_yer", "pr%", ".*", "pr.yer", "(", "[a-z]+"])
async def test_metacharacters_never_widen_the_match(_search_index, _db_session, query):
    assert _ids(_db_session, query) == []


async def test_punctuation_separates_words(_search_index, _db_session):
    _add([{"id": 60, "English_Text": "What is 100% sure? (yes)", "Book": "Muslim"}])
    assert _ids(_db_session, "100%") == [60]
    assert _ids(_db_session, "(yes)") == [60]
    assert _ids(_db_session, "sure yes 100") == [60]


async def test_empty_query_finds_nothing(_search_index, _db_session):
    assert ranking.exact_search(_db_session, "   ", "EN") == {}


# ---------- hybrid ----------


async def test_hybrid_fuses_exact_and_dense(_search_index, _db_session, _fake_model):
    fused = ranking.exact_dense_rrf(_db_session, "الصلاة", "AR", _fake_model)
    assert set(fused) == {1, 2, 3}  # the dense ranking adds the hadith the words miss
    assert list(fused)[0] == 1  # first in both rankings
    assert fused[1] > fused[3] > fused[2]


async def test_hybrid_is_arabic_only(_search_index, _db_session, _fake_model):
    with pytest.raises(ValueError, match="Arabic only"):
        ranking.exact_dense_rrf(_db_session, "prayer", "EN", _fake_model)


# ---------- API ----------


async def test_exact_method_over_the_api(_search_client):
    res = await _search_client.get(SEARCH, params={"q": "prayer fasting", "method": "exact"})
    body = res.json()
    assert [r["hadith"]["hadith_id"] for r in body["results"]] == [3]
    assert "did_you_mean" not in body


async def test_exact_semantic_rrf_over_the_api(_search_client):
    res = await _search_client.get(
        SEARCH, params={"q": "الصلاة", "method": "exact-semantic-rrf", "lang": "ar"}
    )
    assert res.status_code == 200
    assert res.json()["results"][0]["hadith"]["hadith_id"] == 1


async def test_exact_semantic_rrf_refuses_english(_search_client):
    res = await _search_client.get(SEARCH, params={"q": "prayer", "method": "exact-semantic-rrf"})
    assert res.status_code == 422


async def test_new_methods_are_listed(_search_client):
    methods = (await _search_client.get("/api/v1/search-methods")).json()["methods"]
    languages = {m["slug"]: m["languages"] for m in methods}
    assert languages["exact"] == ["en", "ar"]
    assert languages["exact-semantic-rrf"] == ["ar"]
