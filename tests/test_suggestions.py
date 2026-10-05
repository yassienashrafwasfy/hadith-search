"""Autocomplete and did-you-mean (services/suggestions.py, GET /api/v1/suggestions)."""

import pytest

from services import suggestions

URL = "/api/v1/suggestions"
SEARCH = "/api/v1/searches"


def _texts(found):
    return [item["text"] for item in found]


# ---------- fold ----------


def test_fold_lowercases_and_drops_punctuation():
    assert suggestions.fold("  Pray-er! ") == "pray er"


def test_fold_normalises_arabic_like_the_index():
    assert suggestions.fold("الصَّلاة") == "الصلاه"


# ---------- suggest ----------


async def test_prefix_matches_terms_and_chapters(_search_index, _db_session):
    found = suggestions.suggest(_db_session, "pra")
    assert found[0] == {"text": "prayer", "kind": "term", "lang": "en"}
    assert {"text": "Prayer", "kind": "chapter", "lang": "en"} in found


async def test_trigram_similarity_finds_a_near_miss(_search_index, _db_session):
    assert "prayer" in _texts(suggestions.suggest(_db_session, "prayr"))


async def test_arabic_prefix(_search_index, _db_session):
    found = suggestions.suggest(_db_session, "صلا")
    assert found[0] == {"text": "صلاه", "kind": "term", "lang": "ar"}


async def test_prefix_beats_similarity(_search_index, _db_session):
    found = suggestions.suggest(_db_session, "fast")
    assert found[0]["text"] == "fasting"


async def test_limit_is_respected(_search_index, _db_session):
    assert len(suggestions.suggest(_db_session, "r", limit=2)) == 2


async def test_unrelated_text_gives_nothing(_search_index, _db_session):
    assert suggestions.suggest(_db_session, "qqqq") == []
    assert suggestions.suggest(_db_session, "123 !!") == []


@pytest.mark.parametrize("text", ["%", "_", "100%", "a_c", "\\", "' OR 1=1 --", "(", "*"])
async def test_metacharacters_match_nothing_extra(_search_index, _db_session, text):
    assert suggestions.suggest(_db_session, text) == []


# ---------- did_you_mean ----------


async def test_hint_replaces_the_unknown_word(_search_index, _db_session):
    assert suggestions.did_you_mean(_db_session, "prayr", "EN") == "prayer"
    assert suggestions.did_you_mean(_db_session, "prayr fasting", "EN") == "prayer fasting"


async def test_no_hint_when_every_word_is_known(_search_index, _db_session):
    assert suggestions.did_you_mean(_db_session, "prayer fasting", "EN") is None


async def test_no_hint_for_a_word_with_no_close_term(_search_index, _db_session):
    assert suggestions.did_you_mean(_db_session, "xylophone", "EN") is None


async def test_hint_is_per_language(_search_index, _db_session):
    assert suggestions.did_you_mean(_db_session, "prayr", "AR") is None
    assert suggestions.did_you_mean(_db_session, "الصلا", "AR") is None  # a prefix, not a typo


async def test_hint_for_arabic_typo(_search_index, _db_session):
    assert suggestions.did_you_mean(_db_session, "صيان", "AR") == "صيام"


# ---------- API ----------


async def test_suggestions_endpoint(_search_client):
    res = await _search_client.get(URL, params={"q": "pra"})
    assert res.status_code == 200
    body = res.json()
    assert body["suggestions"][0] == {"text": "prayer", "kind": "term", "lang": "en"}
    assert body["_links"]["self"]["href"] == "/api/v1/suggestions?q=pra"
    assert body["_links"]["searches"]["templated"] is True
    assert res.headers["cache-control"] == "public, max-age=300"


async def test_suggestions_are_cacheable(_search_client):
    first = await _search_client.get(URL, params={"q": "pra"})
    again = await _search_client.get(
        URL, params={"q": "pra"}, headers={"If-None-Match": first.headers["etag"]}
    )
    assert again.status_code == 304


async def test_suggestions_limit(_search_client):
    res = await _search_client.get(URL, params={"q": "r", "limit": 1})
    assert len(res.json()["suggestions"]) == 1


@pytest.mark.parametrize(
    "params", [{}, {"q": ""}, {"q": "a" * 101}, {"q": "a", "limit": 0}, {"q": "a", "limit": 11}]
)
async def test_suggestions_bad_input_is_a_problem(_search_client, params):
    res = await _search_client.get(URL, params=params)
    assert res.status_code == 422
    assert res.headers["content-type"].startswith("application/problem+json")


async def test_search_hint_only_when_empty(_search_client):
    miss = (await _search_client.get(SEARCH, params={"q": "prayr", "method": "exact"})).json()
    assert miss["number_of_results"] == 0 and miss["did_you_mean"] == "prayer"
    bm25 = (await _search_client.get(SEARCH, params={"q": "prayr", "method": "bm25"})).json()
    assert bm25["did_you_mean"] == "prayer"
    hit = (await _search_client.get(SEARCH, params={"q": "prayer", "method": "exact"})).json()
    assert "did_you_mean" not in hit


async def test_no_hint_for_methods_that_always_answer(_search_client):
    body = (await _search_client.get(SEARCH, params={"q": "prayr", "method": "tfidf"})).json()
    assert "did_you_mean" not in body


async def test_no_hint_when_nothing_is_close(_search_client):
    body = (await _search_client.get(SEARCH, params={"q": "xylophone", "method": "exact"})).json()
    assert body["number_of_results"] == 0 and "did_you_mean" not in body
