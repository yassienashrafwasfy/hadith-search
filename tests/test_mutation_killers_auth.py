"""Exact-value tests for sign-up, sign-in and query assignment (mutmut survivors in auth.py)."""

import hashlib
import re

import jwt
import pytest
from conftest import TEST_SECRET

from routers import auth

API = "/api/v1"


async def _sign_up(client, name, password="secret123"):
    res = await client.post(f"{API}/annotators", json={"username": name, "password": password})
    return res


def test_a_new_salt_carries_its_iteration_count_and_a_random_part():
    digest, salt = auth.hash_password("pw")
    assert re.fullmatch(r"600000\$[0-9a-f]{32}", salt)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert auth.hash_password("pw")[1] != salt
    iterations, raw = auth._split_salt(salt)
    assert digest == hashlib.pbkdf2_hmac("sha256", b"pw", raw.encode(), iterations).hex()


def test_a_legacy_salt_without_a_count_uses_100000_iterations():
    assert auth._split_salt("abc") == (100_000, "abc")
    assert auth._split_salt("7$abc") == (7, "abc")
    assert auth._split_salt("7$a$b") == (7, "a$b")
    digest, salt = auth.hash_password("pw", "abc")
    assert salt == "abc"
    assert digest == hashlib.pbkdf2_hmac("sha256", b"pw", b"abc", 100_000).hex()
    assert auth.verify_password("pw", digest, "abc")
    assert not auth.verify_password("pw!", digest, "abc")


def test_non_ascii_passwords_hash_as_utf8():
    digest, _ = auth.hash_password("كلمة-سر", "5$s")
    assert digest == hashlib.pbkdf2_hmac("sha256", "كلمة-سر".encode(), b"s", 5).hex()


async def test_sign_up_answer_in_full(_client):
    res = await _sign_up(_client, "alice")
    body = res.json()
    assert res.status_code == 201
    assert set(body) == {"access_token", "token_type", "expires_in", "annotator"}
    assert (body["token_type"], body["expires_in"]) == ("Bearer", 3600)
    annotator = body["annotator"]
    assert annotator["username"] == "alice"
    assert annotator["_links"] == {
        "self": {"href": f"{API}/annotators/{annotator['id']}"},
        "assignments": {"href": f"{API}/assignments"},
    }
    assert annotator["assignments"] == [
        {
            "query_id": "q1",
            "query": "prayer",
            "_links": {"self": {"href": f"{API}/assignments/q1"}},
        },
        {
            "query_id": "q2",
            "query": "fasting",
            "_links": {"self": {"href": f"{API}/assignments/q2"}},
        },
    ]
    claims = jwt.decode(body["access_token"], TEST_SECRET, algorithms=["HS256"])
    assert claims["sub"] == str(annotator["id"]) and claims["name"] == "alice"
    assert claims["exp"] - claims["iat"] == 3600
    assert jwt.get_unverified_header(body["access_token"])["alg"] == "HS256"


async def test_queries_go_to_the_least_assigned_first_and_stop_at_three_annotators(_client):
    plans = []
    for name in ("user1", "user2", "user3", "user4"):
        body = (await _sign_up(_client, name)).json()
        plans.append([a["query_id"] for a in body["annotator"]["assignments"]])
    assert plans == [["q1", "q2"], ["q3", "q1"], ["q2", "q3"], ["q1", "q2"]]
    # q1 and q2 now have three annotators each, so the fifth person only gets q3
    fifth = (await _sign_up(_client, "user5")).json()["annotator"]["assignments"]
    assert [a["query_id"] for a in fifth] == ["q3"]


async def test_a_taken_username_is_409_with_a_message(_client):
    await _sign_up(_client, "alice")
    res = await _sign_up(_client, "alice", "another-pass")
    assert res.status_code == 409 and res.json()["detail"] == "Username already taken"


@pytest.mark.parametrize(
    ("username", "password", "status"),
    [
        ("abc", "12345678", 201),
        ("ab", "12345678", 422),
        ("abc", "1234567", 422),
        ("x" * 64, "p" * 128, 201),
        ("x" * 65, "12345678", 422),
        ("y" * 8, "p" * 129, 422),
    ],
)
async def test_sign_up_length_limits(_client, username, password, status):
    assert (await _sign_up(_client, username, password)).status_code == status


async def test_sign_in_limits_and_messages(_client):
    await _sign_up(_client, "alice")
    ok = await _client.post(f"{API}/tokens", json={"username": "alice", "password": "secret123"})
    body = ok.json()
    assert ok.status_code == 201
    assert (body["token_type"], body["expires_in"]) == ("Bearer", 3600)
    assert [a["query_id"] for a in body["annotator"]["assignments"]] == ["q1", "q2"]
    assert body["annotator"]["assignments"][0]["query"] == "prayer"
    claims = jwt.decode(body["access_token"], TEST_SECRET, algorithms=["HS256"])
    assert claims["name"] == "alice"
    for creds in (
        {"username": "alice", "password": "wrong-pass"},
        {"username": "nobody", "password": "secret123"},
        {"username": "nobody", "password": "not-a-real-password"},
    ):
        bad = await _client.post(f"{API}/tokens", json=creds)
        assert bad.status_code == 401
        assert bad.json()["detail"] == "Invalid username or password"
    too_long = await _client.post(
        f"{API}/tokens", json={"username": "a" * 65, "password": "secret123"}
    )
    assert too_long.status_code == 422
    too_long = await _client.post(
        f"{API}/tokens", json={"username": "alice", "password": "p" * 129}
    )
    assert too_long.status_code == 422
    short_ok = await _client.post(f"{API}/tokens", json={"username": "alice", "password": "x"})
    assert short_ok.status_code == 401  # sign-in has no minimum, only a wrong password


@pytest.mark.parametrize(
    ("headers", "detail"),
    [
        ({}, "Missing or malformed Authorization header"),
        ({"Authorization": "Basic abc"}, "Missing or malformed Authorization header"),
        ({"Authorization": "Bearer"}, "Missing or malformed Authorization header"),
        ({"Authorization": "Bearer junk"}, "Invalid or expired token"),
    ],
)
async def test_authorization_errors_explain_themselves(_client, headers, detail):
    res = await _client.get(f"{API}/annotators/me", headers=headers)
    assert res.status_code == 401 and res.json()["detail"] == detail


async def test_the_bearer_scheme_is_case_insensitive(_client):
    token = (await _sign_up(_client, "alice")).json()["access_token"]
    for scheme in ("bearer", "BEARER", "Bearer"):
        res = await _client.get(
            f"{API}/annotators/me", headers={"Authorization": f"{scheme} {token}"}
        )
        assert res.status_code == 200


async def test_profile_bodies_and_the_self_only_rule(_client):
    signed = (await _sign_up(_client, "alice")).json()
    headers = {"Authorization": f"Bearer {signed['access_token']}"}
    annotator_id = signed["annotator"]["id"]
    me = (await _client.get(f"{API}/annotators/me", headers=headers)).json()
    assert me == {k: v for k, v in signed["annotator"].items()}
    assert (await _client.get(f"{API}/annotators/{annotator_id}", headers=headers)).json() == me
    other = await _client.get(f"{API}/annotators/{annotator_id + 1}", headers=headers)
    assert other.status_code == 403
    assert other.json()["detail"] == "You can only read your own profile"


def test_an_assignment_for_a_query_without_text_has_an_empty_query(_patched_paths):
    (detail,) = auth._assignment_details(["unknown"])
    assert detail["query"] == "" and detail["query_id"] == "unknown"


async def test_a_bearer_value_with_a_space_is_an_invalid_token_not_a_missing_header(_client):
    res = await _client.get(f"{API}/annotators/me", headers={"Authorization": "Bearer a b"})
    assert res.status_code == 401 and res.json()["detail"] == "Invalid or expired token"


async def test_the_profile_is_private_and_revalidated(_client):
    token = (await _sign_up(_client, "alice")).json()["access_token"]
    res = await _client.get(f"{API}/annotators/me", headers={"Authorization": f"Bearer {token}"})
    assert res.headers["cache-control"] == "private, no-cache"
    assert res.headers["etag"]
