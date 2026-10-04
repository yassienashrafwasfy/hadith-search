"""Authentication: bad tokens on every protected route, and one annotator against another."""

import base64
import json
import time

import jwt
import pytest
from conftest import TEST_SECRET
from fastapi.routing import APIRoute

from routers.auth import get_current_annotator
from security._helpers import API, assert_clean

_PATH_VALUES = {"annotator_id": "1", "query_id": "q1", "hadith_id": "1", "pair_id": "1"}
_BODIES = {
    "/assignments/{query_id}/labels/{hadith_id}": {"label": 1},
    "/assignments/{query_id}/progress": {"index": 0},
    "/kv-pairs/{pair_id}": {"status": "verified"},
    "/kv-pairs": [{"id": 1, "status": "verified"}],
}


def _uses_login(dependant) -> bool:
    return any(d.call is get_current_annotator or _uses_login(d) for d in dependant.dependencies)


def _protected_routes(app) -> list[tuple[str, str, str]]:
    """(method, path template, concrete URL) for every route that asks for a login."""
    found = []
    for route in app.routes:
        if isinstance(route, APIRoute) and _uses_login(route.dependant):
            url = route.path
            for name, value in _PATH_VALUES.items():
                url = url.replace("{" + name + "}", value)
            found.extend((m, route.path, url) for m in sorted(route.methods))
    return found


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def _claims(**changes) -> dict:
    now = int(time.time())
    return {"sub": "1", "name": "alice", "iat": now, "exp": now + 600, **changes}


def _forged_tokens() -> dict[str, str]:
    """Every way of presenting a token that must not be accepted."""
    good = jwt.encode(_claims(), TEST_SECRET, algorithm="HS256")
    head, body, sig = good.split(".")
    other_body = _b64(_claims(sub="2"))
    return {
        "expired": jwt.encode(_claims(exp=int(time.time()) - 10), TEST_SECRET, algorithm="HS256"),
        "other secret": jwt.encode(_claims(), "x" * 40, algorithm="HS256"),
        "alg none": jwt.encode(_claims(), None, algorithm="none"),
        "alg none by hand": f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(_claims())}.",
        "alg None by hand": f"{_b64({'alg': 'None', 'typ': 'JWT'})}.{_b64(_claims())}.",
        "alg HS384, right secret": jwt.encode(_claims(), TEST_SECRET, algorithm="HS384"),
        "payload swapped, old signature": f"{head}.{other_body}.{sig}",
        "signature removed": f"{head}.{body}.",
        "no expiry": jwt.encode({"sub": "1", "name": "a"}, TEST_SECRET, algorithm="HS256"),
        "no subject": jwt.encode({"name": "a", "exp": _claims()["exp"]}, TEST_SECRET, "HS256"),
        "subject not a number": jwt.encode(_claims(sub="1 OR 1=1"), TEST_SECRET, "HS256"),
        "garbage": "not-a-token",
        "three empty parts": "..",
        "very long": "A" * 100_000,
    }


def test_the_walker_finds_the_protected_routes(_app):
    """Guards the route walk itself, so a refactor cannot make the 401 tests check nothing."""
    templates = {(m, p) for m, p, _ in _protected_routes(_app)}
    expected = {
        ("GET", f"{API}/annotators/me"),
        ("GET", f"{API}/annotators/{{annotator_id}}"),
        ("GET", f"{API}/assignments"),
        ("GET", f"{API}/assignments/{{query_id}}"),
        ("PUT", f"{API}/assignments/{{query_id}}/labels/{{hadith_id}}"),
        ("PUT", f"{API}/assignments/{{query_id}}/progress"),
        ("GET", f"{API}/agreement"),
        ("GET", f"{API}/kv-pairs"),
        ("GET", f"{API}/kv-pairs/statistics"),
        ("PATCH", f"{API}/kv-pairs/{{pair_id}}"),
        ("PATCH", f"{API}/kv-pairs"),
    }
    assert expected <= templates


def test_only_the_expected_routes_are_public(_app):
    """A new route is public unless it asks for a login: this list makes that a decision."""
    protected = {(m, p) for m, p, _ in _protected_routes(_app)}
    public = {
        (m, r.path)
        for r in _app.routes
        if isinstance(r, APIRoute)
        for m in r.methods
        if (m, r.path) not in protected
    }
    assert public == {
        ("GET", API),
        ("GET", f"{API}/health"),
        ("GET", f"{API}/hadiths/{{hadith_id}}"),
        ("GET", f"{API}/search-methods"),
        ("GET", f"{API}/searches"),
        ("POST", f"{API}/annotators"),
        ("POST", f"{API}/tokens"),
        ("GET", f"{API}/benchmark"),
        ("GET", f"{API}/benchmark/results"),
        ("GET", f"{API}/benchmark/stats"),
        ("GET", f"{API}/benchmark/qrels"),
        ("GET", f"{API}/benchmark/finetuned"),
        ("GET", f"{API}/benchmark/finetuned-stats"),
        ("GET", f"{API}/benchmark/comparison"),
        ("GET", "/{full_path:path}"),  # the frontend
    }


async def _call(api, method, template, url, headers):
    body = _BODIES.get(template.removeprefix(API), {})
    return await api.request(method, url, headers=headers, json=body)


async def test_a_good_token_works_on_every_protected_route(_api, _alice, _app):
    """The control: the 401 tests below are only meaningful if this passes."""
    for method, template, url in _protected_routes(_app):
        res = await _call(_api, method, template, url, _alice["headers"])
        assert res.status_code not in (401, 500), (method, url, res.text)


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Basic YWxpY2U6cGFzcw==",
        "Token abc",
        "bearer",
        "Bearer a b c",
    ],
)
async def test_missing_or_malformed_header_is_refused_everywhere(_api, _app, header):
    headers = {} if header is None else {"Authorization": header}
    for method, template, url in _protected_routes(_app):
        res = await _call(_api, method, template, url, headers)
        assert res.status_code == 401, (method, url, res.status_code)
        assert res.headers["www-authenticate"] == "Bearer"
        assert_clean(res, allow=[401])


@pytest.mark.parametrize("kind", list(_forged_tokens()))
async def test_forged_expired_or_foreign_tokens_are_refused_everywhere(_api, _app, kind):
    token = _forged_tokens()[kind]
    for method, template, url in _protected_routes(_app):
        res = await _call(_api, method, template, url, {"Authorization": f"Bearer {token}"})
        assert res.status_code == 401, (kind, method, url, res.status_code)
        assert_clean(res, allow=[401])


async def test_a_token_in_the_query_string_or_a_cookie_is_not_a_login(_api, _alice):
    token = _alice["headers"]["Authorization"].split()[1]
    assert (await _api.get(f"{API}/assignments", params={"access_token": token})).status_code == 401
    assert (
        await _api.get(f"{API}/assignments", headers={"Cookie": f"access_token={token}"})
    ).status_code == 401


async def test_the_failure_message_does_not_say_why_a_token_failed(_api):
    """Expired, foreign and malformed tokens all get the same answer."""
    answers = set()
    for kind in ("expired", "other secret", "garbage", "alg none"):
        res = await _api.get(
            f"{API}/assignments", headers={"Authorization": f"Bearer {_forged_tokens()[kind]}"}
        )
        answers.add(res.text)
    assert len(answers) == 1


async def test_a_token_for_a_deleted_annotator_reads_nothing(_api, _alice):
    """Tokens are not checked against the table (HANDOFF 17), so a valid one outlives its owner.
    It must at least see no one else's data."""
    ghost = jwt.encode(_claims(sub="99999", name="ghost"), TEST_SECRET, algorithm="HS256")
    res = await _api.get(f"{API}/assignments", headers={"Authorization": f"Bearer {ghost}"})
    assert res.status_code == 200 and res.json()["assignments"] == []
    res = await _api.get(f"{API}/assignments/q1", headers={"Authorization": f"Bearer {ghost}"})
    assert res.status_code == 404


# ---------- one annotator against another ----------


async def test_the_fixture_users_are_set_up_as_the_tests_assume(_alice, _bob):
    assert _alice["queries"] == ["q1", "q2"]
    assert set(_bob["queries"]) == {"q3", "q1"}


async def test_annotators_cannot_read_each_others_profile(_api, _alice, _bob):
    for other_id in (_alice["id"], 1, 2, 3, 0, -1, 99999):
        if other_id == _bob["id"]:
            continue
        res = await _api.get(f"{API}/annotators/{other_id}", headers=_bob["headers"])
        assert res.status_code == 403
        assert_clean(res, allow=[403])
    own = await _api.get(f"{API}/annotators/{_bob['id']}", headers=_bob["headers"])
    assert own.status_code == 200 and own.json()["username"] == "bob"


async def test_the_profile_shows_no_secrets(_api, _alice):
    text = (await _api.get(f"{API}/annotators/me", headers=_alice["headers"])).text
    for word in ("password", "hash", "salt"):
        assert word not in text.lower()


async def test_an_unassigned_query_looks_the_same_as_one_that_does_not_exist(_api, _bob):
    """No way to learn which query ids exist or who has them."""
    unassigned = await _api.get(f"{API}/assignments/q2", headers=_bob["headers"])
    missing = await _api.get(f"{API}/assignments/q99", headers=_bob["headers"])
    assert unassigned.status_code == missing.status_code == 404
    assert unassigned.json() == missing.json()


async def test_another_annotators_assignment_cannot_be_read_or_written(_api, _alice, _bob):
    """q2 is alice's only. Every verb on it is a 404 for bob, and nothing changes."""
    h = _bob["headers"]
    assert (await _api.get(f"{API}/assignments/q2", headers=h)).status_code == 404
    res = await _api.put(f"{API}/assignments/q2/labels/2", headers=h, json={"label": 2})
    assert res.status_code == 404
    res = await _api.put(f"{API}/assignments/q2/progress", headers=h, json={"index": 1})
    assert res.status_code == 404
    mine = await _api.get(f"{API}/assignments/q2", headers=_alice["headers"])
    assert mine.json()["labels"] == {} and mine.json()["current_index"] == 0


async def test_a_listing_only_has_my_own_assignments(_api, _alice, _bob):
    res = await _api.get(f"{API}/assignments", headers=_bob["headers"])
    assert {a["query_id"] for a in res.json()["assignments"]} == {"q1", "q3"}


async def test_labels_and_progress_on_a_shared_query_stay_separate(_api, _alice, _bob):
    """alice and bob both have q1. What one saves never shows up or overwrites for the other."""
    url = f"{API}/assignments/q1"
    assert (
        await _api.put(f"{url}/labels/1", headers=_alice["headers"], json={"label": 2})
    ).status_code == 201
    assert (
        await _api.put(f"{url}/progress", headers=_alice["headers"], json={"index": 1})
    ).status_code == 200

    seen_by_bob = (await _api.get(url, headers=_bob["headers"])).json()
    assert seen_by_bob["labels"] == {} and seen_by_bob["current_index"] == 0

    assert (
        await _api.put(f"{url}/labels/1", headers=_bob["headers"], json={"label": 0})
    ).status_code == 201
    mine = (await _api.get(url, headers=_alice["headers"])).json()
    assert mine["labels"] == {"1": 2} and mine["current_index"] == 1


async def test_ids_in_the_body_or_query_cannot_point_at_someone_else(_api, _alice, _bob):
    """The annotator comes from the token only: extra annotator fields are ignored."""
    res = await _api.put(
        f"{API}/assignments/q1/labels/1",
        headers=_bob["headers"],
        params={"annotator_id": _alice["id"]},
        json={"label": 1, "annotator_id": _alice["id"]},
    )
    assert res.status_code == 201
    alice_view = (await _api.get(f"{API}/assignments/q1", headers=_alice["headers"])).json()
    assert alice_view["labels"] == {}


async def test_the_agreement_report_names_no_annotators(_api, _alice, _bob):
    await _api.put(f"{API}/assignments/q1/labels/1", headers=_alice["headers"], json={"label": 1})
    text = (await _api.get(f"{API}/agreement", headers=_bob["headers"])).text
    assert "alice" not in text and "bob" not in text
    assert "annotator_id" not in text
