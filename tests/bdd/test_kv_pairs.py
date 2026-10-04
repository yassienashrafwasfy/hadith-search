import json

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../../docs/behaviours/kv-pairs.feature")

KV = "/api/v1/kv-pairs"


@given('annotator "alice" holds a token')
def _alice_holds_a_token(_ctx, _auth_headers, _kv_rows):
    _ctx["headers"] = _auth_headers


@when(parsers.re(r"a client calls (?P<method>\w+) /api/v1/kv-pairs(?P<path>\S*) without a token"))
def _call_without_token(_ctx, _api, method, path):
    _ctx["headers"] = {}
    _api(method, f"{KV}{path}")


@when("alice lists kv-pairs")
def _list(_api):
    _api("GET", KV)


@when(parsers.parse('she filters by {field} "{value}"'))
def _filter(_api, field, value):
    _api("GET", KV, params={field: value})


@when("she asks for limit=1 and offset=1")
def _page(_api):
    _api("GET", KV, params={"limit": 1, "offset": 1})


@then(parsers.parse("total is {total:d}"))
def _total(_ctx, total):
    assert _ctx["responses"][0].json()["total"] == total


@then("she gets pair 2 and the links self, first, last, prev and next")
def _page_result(_ctx):
    body = _ctx["responses"][0].json()
    assert [p["id"] for p in body["pairs"]] == [2]
    assert set(body["_links"]) == {"self", "first", "last", "prev", "next"}


@when(parsers.parse("alice lists kv-pairs with {query}"))
def _list_with_query(_api, query):
    _api("GET", f"{KV}?{query}")


@when("alice sends GET /api/v1/kv-pairs/statistics")
def _statistics(_api):
    _api("GET", f"{KV}/statistics")


@then(parsers.re(r"by_status is (?P<expected>\{.*\})"))
def _by_status(_ctx, expected):
    assert _ctx["responses"][0].json()["by_status"] == json.loads(expected)


@then(parsers.re(r"by_topic is (?P<expected>\{.*\})"))
def _by_topic(_ctx, expected):
    assert _ctx["responses"][0].json()["by_topic"] == json.loads(expected)


@when(parsers.parse('alice patches pair 1 with status "{status}"'))
def _patch_one(_api, status):
    _api("PATCH", f"{KV}/1", json={"status": status})


@then(parsers.parse('the status is 200 and the status is "{status}"'))
def _patched(_ctx, status):
    response = _ctx["responses"][0]
    assert response.status_code == 200 and response.json()["status"] == status


@when(parsers.parse('she patches it with status "{status}"'))
def _patch_it(_api, status):
    _api("PATCH", f"{KV}/1", json={"status": status})


@when("she patches pair 99")
def _patch_missing(_api):
    _api("PATCH", f"{KV}/99", json={"status": "verified"})


@when("alice patches /api/v1/kv-pairs with pairs 1 (rejected), 2 (verified) and 99 (verified)")
def _patch_batch(_api):
    items = [
        {"id": 1, "status": "rejected"},
        {"id": 2, "status": "verified"},
        {"id": 99, "status": "verified"},
    ]
    _api("PATCH", KV, json=items)


@then(parsers.re(r"the answer is (?P<expected>\{.*\})"))
def _batch_answer(_ctx, expected):
    assert _ctx["responses"][0].json() == json.loads(expected)


@then("filtering status=verified lists pairs 2 and 3")
def _verified_pairs(_api):
    pairs = _api("GET", KV, params={"status": "verified"}).json()["pairs"]
    assert [p["id"] for p in pairs] == [2, 3]


@when('an item has status "bogus"')
def _bogus_item(_api):
    _api("PATCH", KV, json=[{"id": 1, "status": "bogus"}])
