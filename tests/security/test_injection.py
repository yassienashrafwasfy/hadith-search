"""Injection: hostile text in every parameter, and stored text coming back as plain data."""

import time
from urllib.parse import quote

import pytest
import pytest_asyncio
from sqlalchemy import func, select

import database
from models import Annotator, Hadith, KvPair
from security._helpers import API, PASSWORD, _sign_up, assert_clean

SEARCH = f"{API}/searches"
SQL_PAYLOADS = [
    "' OR '1'='1",
    "'; DROP TABLE hadiths; --",
    '" OR ""="',
    "1; SELECT pg_sleep(8)--",
    "' UNION SELECT password_hash, password_salt, 3 FROM annotators --",
    "%' OR 1=1 --",
    "') OR ('a'='a",
    "\\'; DELETE FROM annotators; --",
    "1 OR 1=1",
    "admin'--",
    "$1; DROP SCHEMA public CASCADE",
    "{{7*7}}${7*7}<%= 7*7 %>",
    "../../../etc/passwd",
    "%00",
    "\r\nSet-Cookie: pwned=1",
]
ODD_TEXT = {
    "right-to-left override": "‮prayer‬",
    "zero width": "pra​yer‍",
    "byte order mark": "﻿prayer",
    "combining marks": "ṕŕáyer" + "́" * 200,
    "arabic": "الصلاة عماد الدين",
    "arabic with marks": "ا" + "ً" * 300 + "ل",
    "emoji": "😀" * 100,
    "full width": "ｐｒａｙｅｒ",
    "500 characters": "a" * 500,
}


async def _counts():
    async with database.get_session() as session:
        return {
            model.__tablename__: (
                await session.execute(select(func.count()).select_from(model))
            ).scalar_one()
            for model in (Hadith, Annotator, KvPair)
        }


@pytest_asyncio.fixture
async def _before(_api):
    return await _counts()


# ---------- SQL injection in every parameter ----------


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
@pytest.mark.parametrize("param", ["q", "method", "lang", "grade_filter", "book_filter"])
async def test_search_parameters_take_sql_payloads_as_plain_text(_api, _before, param, payload):
    params = {"q": "prayer", "method": "bm25"} | {param: payload}
    started = time.monotonic()
    res = await _api.get(SEARCH, params=params)
    assert time.monotonic() - started < 6  # a pg_sleep payload that ran would take 8 s
    if param in ("method", "lang"):
        assert_clean(res, allow=[422], echoed=payload)
    else:
        assert_clean(res, allow=[200, 422])
    assert await _counts() == _before


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
async def test_a_filter_payload_never_widens_the_result(_api, payload):
    """`' OR '1'='1` in a filter must match nothing, not everything."""
    everything = (await _api.get(SEARCH, params={"q": "prayer", "method": "bm25"})).json()
    assert everything["number_of_results"] > 0
    for field in ("grade_filter", "book_filter"):
        res = await _api.get(SEARCH, params={"q": "prayer", "method": "bm25", field: payload})
        assert res.status_code == 200
        assert res.json()["number_of_results"] == 0


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
async def test_hadith_id_in_the_path_must_be_a_number(_api, payload):
    res = await _api.get(f"{API}/hadiths/{quote(payload, safe='')}")
    assert_clean(res, allow=[404, 422])


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
@pytest.mark.parametrize("field", ["status", "topic"])
async def test_kv_pair_filters_take_sql_payloads_as_plain_text(
    _api, _alice, _kv_pair, _before, field, payload
):
    res = await _api.get(f"{API}/kv-pairs", params={field: payload}, headers=_alice["headers"])
    assert_clean(res, allow=[200])
    assert res.json()["total"] == 0
    assert await _counts() == _before


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
async def test_annotation_paths_and_bodies_take_sql_payloads_as_plain_text(
    _api, _alice, _before, payload
):
    h, p = _alice["headers"], quote(payload, safe="")
    for method, url, body in (
        ("GET", f"{API}/assignments/{p}", None),
        ("PUT", f"{API}/assignments/{p}/labels/1", {"label": 1}),
        ("PUT", f"{API}/assignments/{p}/progress", {"index": 0}),
        ("PUT", f"{API}/assignments/q1/labels/{p}", {"label": 1}),
        ("PUT", f"{API}/assignments/q1/labels/1", {"label": payload}),
        ("PUT", f"{API}/assignments/q1/progress", {"index": payload}),
        ("PATCH", f"{API}/kv-pairs/{p}", {"status": "verified"}),
        ("PATCH", f"{API}/kv-pairs/1", {"status": payload}),
        ("PATCH", f"{API}/kv-pairs", [{"id": payload, "status": "verified"}]),
        ("GET", f"{API}/annotators/{p}", None),
    ):
        res = await _api.request(method, url, headers=h, json=body)
        assert_clean(res, allow=[404, 405, 422])
    assert await _counts() == _before


@pytest.mark.parametrize(
    "mode", ["../../etc/passwd", "..%2f..%2fsecret", "a/b", "combined\n", "x y", "é", "a;b", ""]
)
async def test_the_benchmark_mode_cannot_reach_other_files(_api, mode):
    res = await _api.get(f"{API}/benchmark/finetuned", params={"mode": mode})
    assert_clean(res, allow=[404, 422])
    assert res.status_code == 422


async def test_signin_cannot_be_bypassed_with_sql(_api, _alice):
    for name, password in (
        ("' OR '1'='1", "x" * 8),
        ("alice'--", "x" * 8),
        ("alice", "' OR '1'='1"),
    ):
        assert_clean(
            await _api.post(f"{API}/tokens", json={"username": name, "password": password}),
            allow=[401],
        )


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
async def test_sql_payload_usernames_are_stored_as_text_and_found_by_exact_match_only(
    _api, _before, payload
):
    if "\x00" in payload:
        pytest.skip("covered by the NUL test in test_accounts.py")
    name = ("x" + payload)[:64]
    user = await _sign_up(_api, name)
    me = (await _api.get(f"{API}/annotators/me", headers=user["headers"])).json()
    assert me["username"] == name
    counts = await _counts()
    assert (
        counts["annotators"] == _before["annotators"] + 1
        and counts["hadiths"] == _before["hadiths"]
    )
    # a prefix of the payload (what a LIKE or an injected OR would match) is another, unknown user
    res = await _api.post(f"{API}/tokens", json={"username": "x", "password": PASSWORD})
    assert res.status_code == 401


# ---------- odd text ----------


@pytest.mark.parametrize("name", list(ODD_TEXT))
async def test_odd_unicode_in_a_search_is_answered_normally(_api, name):
    for method in ("bm25", "tfidf", "term-overlap"):
        res = await _api.get(SEARCH, params={"q": ODD_TEXT[name], "method": method})
        assert_clean(res, allow=[200, 422])


async def test_a_search_is_capped_at_500_characters(_api):
    assert (await _api.get(SEARCH, params={"q": "a" * 500, "method": "bm25"})).status_code == 200
    long = await _api.get(SEARCH, params={"q": "a" * 501, "method": "bm25"})
    assert_clean(long, allow=[422])
    assert_clean(await _api.get(SEARCH, params={"q": "", "method": "bm25"}), allow=[422])


@pytest.mark.parametrize("field", ["q", "grade_filter", "book_filter"])
async def test_a_nul_character_in_a_search_is_a_4xx_not_a_500(_api, field):
    params = {"q": "prayer", "method": "bm25"} | {field: "pra\x00yer"}
    assert_clean(await _api.get(SEARCH, params=params), allow=[200, 422])


# ---------- stored text comes back as data ----------


XSS = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "javascript:alert(1)",
    "</script><svg/onload=alert(1)>",
]


@pytest.mark.parametrize("text", XSS)
async def test_stored_markup_is_returned_as_json_text_not_html(_api, _alice, text):
    user = await _sign_up(_api, "x" + text)
    async with database.get_session() as session:
        session.add(
            KvPair(
                topic=text,
                language="en",
                concept_en=text,
                concept_ar=text,
                entity_en=text,
                entity_ar=text,
                hadith_id=1,
                status="pending",
                created_at=database.now_iso(),
            )
        )
        await session.commit()
    for res in (
        await _api.get(f"{API}/annotators/me", headers=user["headers"]),
        await _api.get(f"{API}/kv-pairs", headers=_alice["headers"]),
        await _api.post(f"{API}/tokens", json={"username": "x" + text, "password": PASSWORD}),
    ):
        assert res.status_code in (200, 201)
        assert res.headers["content-type"].startswith("application/json")
        assert res.headers["x-content-type-options"] == "nosniff"
    me = (await _api.get(f"{API}/annotators/me", headers=user["headers"])).json()
    assert me["username"] == "x" + text
    pairs = (await _api.get(f"{API}/kv-pairs", headers=_alice["headers"])).json()["pairs"]
    assert pairs[0]["topic"] == text and pairs[0]["entity_en"] == text


async def test_stored_text_survives_a_round_trip_byte_for_byte(_api):
    for i, text in enumerate(("‮evil‬", "اسم" * 10, "😀" * 20, "a\tb", "x\\ny")):
        user = await _sign_up(_api, f"u{i}-{text}")
        me = (await _api.get(f"{API}/annotators/me", headers=user["headers"])).json()
        assert me["username"] == f"u{i}-{text}"
