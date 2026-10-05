"""Suggestions and the did-you-mean hint take hostile text as plain data and bound their own work."""

import time

import pytest

from security._helpers import API, assert_clean
from security.test_injection import ODD_TEXT, SQL_PAYLOADS

SUGGESTIONS = f"{API}/suggestions"
SEARCH = f"{API}/searches"
METACHARACTERS = ["%", "_", "%%%%", "\\", "\\%", "*", ".*", "(", "[", "{1,", "a|b", "$", "^", "~"]


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
async def test_sql_looking_input_is_plain_text(_api, payload):
    started = time.monotonic()
    res = await _api.get(SUGGESTIONS, params={"q": payload})
    assert time.monotonic() - started < 6  # a pg_sleep payload that ran would take 8 s
    assert_clean(res, allow=[200, 422], echoed=payload)
    if res.status_code == 200:
        assert res.json()["suggestions"] == []


@pytest.mark.parametrize("payload", METACHARACTERS)
async def test_like_and_regex_metacharacters_match_nothing_extra(_api, payload):
    for url, params in (
        (SUGGESTIONS, {"q": payload}),
        (SEARCH, {"q": payload, "method": "bm25"}),
    ):
        res = await _api.get(url, params=params)
        assert_clean(res, allow=[200])
        body = res.json()
        assert body.get("suggestions", body.get("results")) == []


async def test_a_nul_byte_is_a_422(_api):
    for url, params in (
        (SUGGESTIONS, {"q": "pra\x00y"}),
        (SEARCH, {"q": "pra\x00y", "method": "bm25"}),
    ):
        assert_clean(await _api.get(url, params=params), allow=[422])


async def test_very_long_input_is_refused(_api):
    res = await _api.get(SUGGESTIONS, params={"q": "a" * 5000})
    assert_clean(res, allow=[422])
    res = await _api.get(SEARCH, params={"q": "a " * 5000, "method": "bm25"})
    assert_clean(res, allow=[422])


async def test_many_query_words_stay_fast(_api):
    started = time.monotonic()
    res = await _api.get(SEARCH, params={"q": " ".join(["prayer"] * 200)[:500], "method": "bm25"})
    assert_clean(res, allow=[200])
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("limit", ["0", "-1", "11", "1000000", "x", "1.5", "99999999999999999999"])
async def test_limit_abuse_is_a_422(_api, limit):
    assert_clean(await _api.get(SUGGESTIONS, params={"q": "pr", "limit": limit}), allow=[422])


@pytest.mark.parametrize("text", list(ODD_TEXT.values()))
async def test_odd_unicode_is_handled(_api, text):
    res = await _api.get(SUGGESTIONS, params={"q": text[:100]})
    assert_clean(res, allow=[200])
    res = await _api.get(SEARCH, params={"q": text, "method": "bm25"})
    assert_clean(res, allow=[200])


async def test_a_sql_payload_finds_nothing_and_hints_nothing(_api):
    res = await _api.get(SEARCH, params={"q": "' OR '1'='1", "method": "bm25"})
    assert_clean(res, allow=[200])
    body = res.json()
    assert body["number_of_results"] == 0
    assert "did_you_mean" not in body
