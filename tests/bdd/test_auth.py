import jwt
from conftest import TEST_SECRET
from pytest_bdd import given, parsers, scenarios, then, when

scenarios("../../docs/behaviours/auth.feature")

API = "/api/v1"
ME = f"{API}/annotators/me"


def _sign_up(api, username, password):
    return api("POST", f"{API}/annotators", json={"username": username, "password": password})


@when('a client sends POST /api/v1/annotators with username "alice" and password "secret123"')
def _sign_up_alice(_api):
    _sign_up(_api, "alice", "secret123")


@then(parsers.parse("the Location header is /api/v1/annotators/<id>"))
def _location(_ctx):
    response = _ctx["responses"][0]
    assert response.headers["Location"] == f"{API}/annotators/{response.json()['annotator']['id']}"


@then('the body has token_type "Bearer" and expires_in 3600')
def _token_body(_ctx):
    body = _ctx["responses"][0].json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 3600 and body["access_token"]


@then("the annotator has 2 assigned queries")
def _two_queries(_ctx):
    assert len(_ctx["responses"][0].json()["annotator"]["assignments"]) == 2


@when(parsers.parse('a client signs up with username "{username}" and password "{password}"'))
def _sign_up_with(_api, username, password):
    _sign_up(_api, username, password)


@then(parsers.parse('the errors name the field "{field}"'))
def _error_field(_ctx, field):
    assert [e["field"] for e in _ctx["responses"][0].json()["errors"]] == [field]


@given('"alice" is already signed up')
def _alice_exists(_auth_headers):
    """The fixture signs alice up with the password secret123."""


@when('a client signs up as "alice" again')
def _sign_up_again(_api):
    _sign_up(_api, "alice", "secret123")


@then('the status is 409 and the title is "Conflict"')
def _conflict(_ctx):
    response = _ctx["responses"][0]
    assert response.status_code == 409 and response.json()["title"] == "Conflict"


@given("3 annotators have signed up")
def _three_signed_up(_ctx, _api):
    held = [
        {
            a["query_id"]
            for a in _sign_up(_api, f"user{i}", "secret123").json()["annotator"]["assignments"]
        }
        for i in range(3)
    ]
    _ctx["held_by"] = {
        q: sum(q in queries for queries in held) for queries in held for q in queries
    }


@when("a 4th signs up")
def _fourth_signs_up(_api):
    _sign_up(_api, "user3", "secret123")


@then("the 4th still gets 2 queries, and none of them is one already held by 3 people")
def _fourth_queries(_ctx):
    queries = [a["query_id"] for a in _ctx["responses"][0].json()["annotator"]["assignments"]]
    assert len(queries) == 2
    assert all(_ctx["held_by"].get(q, 0) < 3 for q in queries), (queries, _ctx["held_by"])


@when("a client sends POST /api/v1/tokens with the right username and password")
def _sign_in(_api):
    _api("POST", f"{API}/tokens", json={"username": "alice", "password": "secret123"})


@then("the status is 201 and the body holds an access_token")
def _signed_in(_ctx):
    response = _ctx["responses"][0]
    assert response.status_code == 201 and response.json()["access_token"]
    _ctx["token"] = response.json()["access_token"]


@when("the client sends GET /api/v1/annotators/me with that token as a Bearer header")
def _read_profile(_api, _ctx):
    _api("GET", ME, headers={"Authorization": f"Bearer {_ctx['token']}"})


@then("it receives the annotator's profile")
def _profile(_ctx):
    assert _ctx["responses"][0].json()["username"] == "alice"


@when("a client signs in with an unknown username")
def _unknown_user(_ctx, _api):
    unknown = _api("POST", f"{API}/tokens", json={"username": "nobody", "password": "secret123"})
    _ctx["unknown"] = unknown


@when("another signs in with a known username and a wrong password")
def _wrong_password(_ctx, _api):
    wrong = _api("POST", f"{API}/tokens", json={"username": "alice", "password": "wrong-pass"})
    _ctx["wrong"] = wrong


@then("both get the same status and the same body")
def _same_answer(_ctx):
    unknown, wrong = _ctx["unknown"], _ctx["wrong"]
    assert unknown.status_code == 401
    assert (unknown.status_code, unknown.json()) == (wrong.status_code, wrong.json())


def _credentials(kind: str) -> dict:
    forged = jwt.encode({"sub": "1", "name": "x", "exp": 9999999999}, "other-" * 8, "HS256")
    expired = jwt.encode({"sub": "1", "name": "x", "exp": 1}, TEST_SECRET, "HS256")
    return {
        "no Authorization header": {},
        "a malformed Authorization header": {"Authorization": "Token x"},
        "a token signed with another secret": {"Authorization": f"Bearer {forged}"},
        "an expired token": {"Authorization": f"Bearer {expired}"},
        "a string that is not a JWT": {"Authorization": "Bearer not-a-jwt"},
    }[kind]


@when(parsers.parse("a client calls GET /api/v1/annotators/me with {credentials}"))
def _call_me(_api, credentials):
    _api("GET", ME, headers=_credentials(credentials))


@then('the WWW-Authenticate header is "Bearer"')
def _www_authenticate(_ctx):
    assert _ctx["responses"][0].headers["www-authenticate"] == "Bearer"


@given("a token for annotator A")
def _token_for_a(_ctx, _api, _auth_headers):
    _ctx["headers"] = _auth_headers
    _ctx["a_id"] = _api("GET", ME).json()["id"]


@when("the client asks for GET /api/v1/annotators/<B's id>")
def _ask_for_b(_ctx, _api):
    _api("GET", f"{API}/annotators/{_ctx['a_id'] + 1}")
