from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../../docs/behaviours/annotation.feature")

API = "/api/v1"


@given('annotator "alice" has signed up and holds a token')
def _alice_signed_up(_ctx, _auth_headers):
    _ctx["headers"] = _auth_headers


@given('"alice" is assigned queries q1 and q2, each with a pool of 2 hadiths')
def _alice_has_two_queries(_api):
    listing = _api("GET", f"{API}/assignments").json()["assignments"]
    assert [(a["query_id"], a["total"]) for a in listing] == [("q1", 2), ("q2", 2)]


@when("alice sends GET /api/v1/assignments")
def _list_assignments(_api):
    _api("GET", f"{API}/assignments")


@then("each assignment shows query_id, total, graded and current_index")
def _assignment_fields(_ctx):
    for item in _ctx["responses"][0].json()["assignments"]:
        assert {"query_id", "total", "graded", "current_index"} <= set(item)


@then("q1 shows total 2 and graded 0")
def _q1_totals(_ctx):
    q1 = next(a for a in _ctx["responses"][0].json()["assignments"] if a["query_id"] == "q1")
    assert (q1["total"], q1["graded"]) == (2, 0)


@when("alice sends GET /api/v1/assignments/q1")
def _open_q1(_api):
    _api("GET", f"{API}/assignments/q1")


@then(
    "the body has the query text, the pooled hadiths with their texts, her labels "
    "and current_index"
)
def _assignment_body(_ctx):
    body = _ctx["responses"][0].json()
    assert body["query"] and "labels" in body and "current_index" in body
    assert [h["english_hadith"] for h in body["pooled_hadiths"]]


@then('the links include a templated "label" link')
def _label_link(_ctx):
    label = _ctx["responses"][0].json()["_links"]["label"]
    assert label["templated"] is True and "{hadith_id}" in label["href"]


@when(parsers.parse("alice asks for /api/v1/assignments/{query_id}"))
def _ask_for_assignment(_api, query_id):
    _api("GET", f"{API}/assignments/{query_id}")


@when("a client calls GET /api/v1/assignments without a token")
def _no_token(_ctx, _api):
    _ctx["headers"] = {}
    _api("GET", f"{API}/assignments")


@when("alice puts label 0 on hadith 1 of q1")
def _first_label(_api):
    _api("PUT", f"{API}/assignments/q1/labels/1", json={"label": 0})


@when("she puts label 2 on the same hadith")
def _second_label(_api):
    _api("PUT", f"{API}/assignments/q1/labels/1", json={"label": 2})


@then("the status is 200 and the stored label is 2")
def _label_replaced(_ctx, _api):
    response = _ctx["responses"][0]
    assert response.status_code == 200 and response.json()["label"] == 2
    assert _api("GET", f"{API}/assignments/q1").json()["labels"] == {"1": 2}


@when("alice puts label 5 on a hadith of q1")
def _bad_label(_api):
    _api("PUT", f"{API}/assignments/q1/labels/1", json={"label": 5})


@when("she labels a hadith that is not in the pool of q1")
def _hadith_outside_pool(_api):
    _api("PUT", f"{API}/assignments/q1/labels/2", json={"label": 1})


@when("alice puts progress index 1 on q1")
def _progress_one(_api):
    _api("PUT", f"{API}/assignments/q1/progress", json={"index": 1})


@then("the status is 200 and GET /api/v1/assignments/q1 shows current_index 1")
def _progress_saved(_ctx, _api):
    assert _ctx["responses"][0].status_code == 200
    assert _api("GET", f"{API}/assignments/q1").json()["current_index"] == 1


@when("she puts index 9 or -1")
def _bad_progress(_api):
    url = f"{API}/assignments/q1/progress"
    _api.many([("PUT", url, {"json": {"index": 9}}), ("PUT", url, {"json": {"index": -1}})])


@when("alice sends GET /api/v1/agreement")
def _agreement(_api):
    _api("GET", f"{API}/agreement")


@then("the body has per_query, overall and _links")
def _agreement_body(_ctx):
    assert {"per_query", "overall", "_links"} <= set(_ctx["responses"][0].json())
