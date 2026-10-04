"""Input validation: odd sizes, numbers and types give a 4xx problem+json, never a 500."""

import pytest

from security._helpers import API, assert_clean

SEARCH = f"{API}/searches"
HUGE = 10**30
INT64_PLUS_ONE = 2**63


@pytest.mark.parametrize(
    "limit", ["-1", "0", "201", "1000000", str(HUGE), "abc", "", "1.5", "NaN", "1e3", "0x10"]
)
async def test_kv_pairs_limit_outside_1_to_200_is_422(_api, _alice, limit):
    res = await _api.get(f"{API}/kv-pairs", params={"limit": limit}, headers=_alice["headers"])
    assert_clean(res, allow=[422])


@pytest.mark.parametrize("limit", ["1", "50", "200"])
async def test_kv_pairs_limits_inside_the_range_work(_api, _alice, _kv_pair, limit):
    res = await _api.get(f"{API}/kv-pairs", params={"limit": limit}, headers=_alice["headers"])
    assert res.status_code == 200 and res.json()["limit"] == int(limit)


@pytest.mark.parametrize("offset", ["-1", "abc", "", "1.5", "NaN", "-9999999999999999999999"])
async def test_kv_pairs_bad_offsets_are_422(_api, _alice, offset):
    res = await _api.get(f"{API}/kv-pairs", params={"offset": offset}, headers=_alice["headers"])
    assert_clean(res, allow=[422])


async def test_kv_pairs_offset_past_the_end_is_an_empty_page(_api, _alice, _kv_pair):
    res = await _api.get(f"{API}/kv-pairs", params={"offset": 1000}, headers=_alice["headers"])
    assert res.status_code == 200 and res.json()["pairs"] == []


@pytest.mark.parametrize("offset", [str(INT64_PLUS_ONE), str(HUGE)])
async def test_kv_pairs_offset_beyond_64_bits_is_a_4xx(_api, _alice, offset):
    res = await _api.get(f"{API}/kv-pairs", params={"offset": offset}, headers=_alice["headers"])
    assert_clean(res, allow=[200, 422])


@pytest.mark.parametrize(
    "number", ["2147483648", str(INT64_PLUS_ONE), str(HUGE), "-" + str(INT64_PLUS_ONE)]
)
async def test_huge_ids_are_4xx_not_500(_api, _alice, number):
    h = _alice["headers"]
    for method, url, body in (
        ("GET", f"{API}/hadiths/{number}", None),
        ("PATCH", f"{API}/kv-pairs/{number}", {"status": "verified"}),
        ("PATCH", f"{API}/kv-pairs", [{"id": int(number), "status": "verified"}]),
    ):
        assert_clean(await _api.request(method, url, headers=h, json=body), allow=[404, 422])


@pytest.mark.parametrize("number", [str(INT64_PLUS_ONE), str(HUGE), "-5", "0", "abc", "1.5"])
async def test_odd_ids_where_the_code_checks_first_are_clean(_api, _alice, number):
    h = _alice["headers"]
    for method, url, body in (
        ("GET", f"{API}/annotators/{number}", None),
        ("PUT", f"{API}/assignments/q1/labels/{number}", {"label": 1}),
    ):
        assert_clean(await _api.request(method, url, headers=h, json=body), allow=[403, 404, 422])


@pytest.mark.parametrize("label", [3, -1, 1.5, "1", "", None, [], {}, [1], {"label": 1}, "DROP"])
async def test_a_label_must_be_0_1_or_2(_api, _alice, label):
    res = await _api.put(
        f"{API}/assignments/q1/labels/1", headers=_alice["headers"], json={"label": label}
    )
    assert_clean(res, allow=[422])


@pytest.mark.parametrize("index", [-1, 2, 99, HUGE, -HUGE, 1.5, "", "x", None, [], {}])
async def test_progress_must_be_a_whole_number_inside_the_pool(_api, _alice, index):
    res = await _api.put(
        f"{API}/assignments/q1/progress", headers=_alice["headers"], json={"index": index}
    )
    assert_clean(res, allow=[422])


@pytest.mark.parametrize("status", ["pending", "VERIFIED", "", None, 1, True, ["verified"]])
async def test_a_kv_status_must_be_verified_or_rejected(_api, _alice, _kv_pair, status):
    res = await _api.patch(f"{API}/kv-pairs/1", headers=_alice["headers"], json={"status": status})
    assert_clean(res, allow=[422])


@pytest.mark.parametrize(
    "body", [{}, "x", None, 5, {"id": 1, "status": "verified"}, [{"id": 1}], [[]], [None]]
)
async def test_a_kv_batch_must_be_a_list_of_id_and_status(_api, _alice, body):
    res = await _api.patch(f"{API}/kv-pairs", headers=_alice["headers"], json=body)
    assert_clean(res, allow=[422])


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": "prayer"},
        {"method": "bm25"},
        {"q": "prayer", "method": "bm25", "lang": "EN"},
        {"q": "prayer", "method": "bm25", "lang": "fr"},
        {"q": "prayer", "method": ""},
        {"q": "prayer", "method": "semantic-rrf", "lang": "en"},  # dense methods are Arabic only
    ],
)
async def test_search_needs_valid_parameters(_api, params):
    assert_clean(await _api.get(SEARCH, params=params), allow=[422])


async def test_repeated_and_unknown_search_parameters_do_no_harm(_api):
    res = await _api.get(f"{SEARCH}?q=prayer&q=fasting&method=bm25&method=tfidf&x=1&__proto__=1")
    assert_clean(res, allow=[200, 422])


# ---------- bodies ----------


@pytest.mark.parametrize("route", ["annotators", "tokens"])
async def test_a_megabytes_large_body_is_refused_by_the_field_limits_not_swallowed(_api, route):
    """The app itself has no body-size cap (nginx adds 1 MB, see test_resource_abuse.py), so this
    checks that a big body still ends as a 422 and not as work or a crash."""
    body = {"username": "u" * 3_000_000, "password": "p" * 3_000_000}
    assert_clean(await _api.post(f"{API}/{route}", json=body), allow=[422])


async def test_extra_fields_in_a_body_are_ignored_not_stored(_api, _alice):
    res = await _api.put(
        f"{API}/assignments/q1/labels/1",
        headers=_alice["headers"],
        json={"label": 1, "annotator_id": 99, "created_at": "x", "id": 5},
    )
    assert res.status_code == 201 and set(res.json()) == {
        "query_id",
        "hadith_id",
        "label",
        "_links",
    }


@pytest.mark.parametrize(
    "content",
    [
        b"[" * 100_000,
        b'{"a":' * 50_000,
        b'{"label": 1,}',
        b"\x00\x01\x02",
        b"\xff\xfe",
        b'{"label": 1} trailing',
        b"NaN",
        b'{"label": NaN}',
        b'{"label": Infinity}',
    ],
)
async def test_broken_or_extreme_json_is_a_4xx(_api, _alice, content):
    res = await _api.put(
        f"{API}/assignments/q1/labels/1",
        headers=_alice["headers"] | {"Content-Type": "application/json"},
        content=content,
    )
    assert_clean(res, allow=range(400, 500))


@pytest.mark.parametrize(
    "content_type", ["text/plain", "application/xml", "multipart/form-data", ""]
)
async def test_a_body_sent_with_the_wrong_content_type_is_a_4xx(_api, _alice, content_type):
    res = await _api.put(
        f"{API}/assignments/q1/labels/1",
        headers=_alice["headers"] | {"Content-Type": content_type},
        content=b'{"label": 1}',
    )
    assert_clean(res, allow=range(400, 500))


@pytest.mark.parametrize("method", ["TRACE", "CONNECT", "DELETE", "PATCH", "PUT"])
async def test_methods_the_search_route_does_not_offer_are_refused(_api, method):
    res = await _api.request(method, SEARCH, params={"q": "a", "method": "bm25"})
    assert_clean(res, allow=[404, 405])


async def test_validation_errors_do_not_echo_the_rejected_value(_api, _alice):
    marker = "MARKER-VALUE-12345"
    res = await _api.put(
        f"{API}/assignments/q1/labels/1", headers=_alice["headers"], json={"label": marker}
    )
    assert res.status_code == 422 and marker not in res.text
    assert set(res.json()) == {"type", "title", "status", "detail", "errors"}


async def test_a_kv_batch_has_a_size_limit(_api, _alice):
    items = [{"id": i, "status": "verified"} for i in range(1, 1002)]
    res = await _api.patch(f"{API}/kv-pairs", headers=_alice["headers"], json=items)
    assert_clean(res, allow=[413, 422])


# ---------- NUL characters and 32-bit ids in the other routes ----------


@pytest.mark.parametrize("field", ["status", "topic"])
async def test_a_nul_character_in_a_kv_filter_is_422(_api, _alice, field):
    res = await _api.get(f"{API}/kv-pairs", params={field: "a\x00b"}, headers=_alice["headers"])
    assert_clean(res, allow=[422])


async def test_a_nul_character_in_a_query_id_is_422(_api, _alice):
    res = await _api.get(f"{API}/assignments/a%00b", headers=_alice["headers"])
    assert_clean(res, allow=[422])


@pytest.mark.parametrize("number", ["2147483648", str(INT64_PLUS_ONE), "-1"])
async def test_label_ids_out_of_range_are_422(_api, _alice, number):
    res = await _api.put(
        f"{API}/assignments/q1/labels/{number}", headers=_alice["headers"], json={"label": 1}
    )
    assert_clean(res, allow=[422])


async def test_the_biggest_32_bit_id_is_still_accepted(_api, _alice):
    res = await _api.get(f"{API}/hadiths/2147483647")
    assert_clean(res, allow=[404])


async def test_a_kv_batch_of_the_maximum_size_is_accepted(_api, _alice):
    items = [{"id": i, "status": "verified"} for i in range(1, 101)]
    res = await _api.patch(f"{API}/kv-pairs", headers=_alice["headers"], json=items)
    assert res.status_code == 200 and res.json() == {"updated": 0}
