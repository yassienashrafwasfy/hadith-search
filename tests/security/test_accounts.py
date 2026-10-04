"""Sign-up and sign-in: input limits, edge cases, no user enumeration, password handling."""

import logging

import pytest
from sqlalchemy import select

import database
import routers.auth as auth_module
from models import Annotator
from security._helpers import API, PASSWORD, assert_clean


async def _signup(api, username, password=PASSWORD):
    return await api.post(f"{API}/annotators", json={"username": username, "password": password})


async def _signin(api, username, password):
    return await api.post(f"{API}/tokens", json={"username": username, "password": password})


async def _stored(username):
    async with database.get_session() as session:
        rows = await session.execute(select(Annotator).where(Annotator.username == username))
        return rows.scalar_one_or_none()


# ---------- limits ----------


@pytest.mark.parametrize(
    ("username", "password", "ok"),
    [
        ("abc", "12345678", True),  # both minimums
        ("ab", "12345678", False),
        ("", "12345678", False),
        ("a" * 64, "p" * 128, True),  # both maximums
        ("a" * 65, "12345678", False),
        ("abc", "1234567", False),
        ("abc", "p" * 129, False),
        ("abc", "", False),
    ],
)
async def test_signup_length_limits_are_enforced(_api, username, password, ok):
    res = await _signup(_api, username, password)
    assert res.status_code == (201 if ok else 422)
    assert_clean(res, allow=[201, 422])


@pytest.mark.parametrize("field", ["username", "password"])
async def test_signin_caps_input_before_hashing(_api, field, monkeypatch):
    """A huge password must not reach PBKDF2 (a cheap way to burn CPU)."""
    calls = []
    monkeypatch.setattr(auth_module, "hash_password", lambda *a, **k: calls.append(a) or ("", ""))
    body = {"username": "alice", "password": "x"}
    body[field] = "x" * 1_000_000
    res = await _api.post(f"{API}/tokens", json=body)
    assert res.status_code == 422 and calls == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"username": "alice"},
        {"password": PASSWORD},
        {"username": None, "password": None},
        {"username": 123, "password": 12345678},
        {"username": ["a", "b", "c"], "password": {"x": 1}},
        {"username": True, "password": False},
        [],
        "just a string",
        None,
    ],
)
@pytest.mark.parametrize("route", ["annotators", "tokens"])
async def test_wrong_shapes_and_types_are_422_not_500(_api, route, body):
    res = await _api.post(f"{API}/{route}", json=body)
    assert res.status_code == 422
    assert_clean(res, allow=[422])


@pytest.mark.parametrize("route", ["annotators", "tokens"])
async def test_non_json_and_broken_json_are_4xx(_api, route):
    for content in (b"{", b"", b"\xff\xfe\x00", b"username=a&password=b"):
        res = await _api.post(f"{API}/{route}", content=content)
        assert_clean(res, allow=range(400, 500))
    res = await _api.post(
        f"{API}/{route}",
        content=b"username=alice&password=whatever1",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert_clean(res, allow=[422])


# ---------- username and password edge cases ----------


@pytest.mark.parametrize(
    "username",
    [
        "   ",
        "a b c",
        "tab\tname",
        "new\nline",
        "Ünïcödé-ñame",
        "اسم المستخدم",
        "emoji-😀-name",
        "rtl‮evil",
        "zero​width",
        "<script>alert(1)</script>",
        "'; DROP TABLE annotators; --",
        "../../etc/passwd",
        "%00%0d%0a",
    ],
)
async def test_odd_usernames_are_kept_as_typed_and_never_break_anything(_api, username):
    res = await _signup(_api, username)
    assert res.status_code == 201, res.text
    assert res.json()["annotator"]["username"] == username
    assert_clean(await _signin(_api, username, PASSWORD), allow=[201])
    assert (await _stored(username)) is not None


async def test_a_nul_character_in_a_username_is_a_4xx(_api):
    """PostgreSQL text cannot hold NUL; the app must refuse it before the database does."""
    res = await _signup(_api, "bad\x00name")
    assert_clean(res, allow=range(400, 500))
    assert_clean(await _signin(_api, "bad\x00name", PASSWORD), allow=range(400, 500))


async def test_a_nul_character_in_a_password_is_fine_or_4xx(_api):
    res = await _signup(_api, "nulpass", "pass\x00word1")
    assert_clean(res, allow=[201, 422])
    if res.status_code == 201:
        assert (await _signin(_api, "nulpass", "pass\x00word1")).status_code == 201
        assert (await _signin(_api, "nulpass", "pass")).status_code == 401


async def test_the_password_is_checked_in_full(_api):
    await _signup(_api, "carol", "p" * 128)
    assert (await _signin(_api, "carol", "p" * 128)).status_code == 201
    assert (await _signin(_api, "carol", "p" * 127)).status_code == 401
    assert (await _signin(_api, "carol", " " + "p" * 127)).status_code == 401


async def test_usernames_that_differ_only_by_case_or_spaces_are_separate_accounts(_api):
    """DECISION (owner): no normalisation. 'dave' and 'Dave' are two accounts on purpose."""
    assert (await _signup(_api, "dave")).status_code == 201
    for twin in ("Dave", "DAVE", "dave "):
        assert (await _signup(_api, twin)).status_code == 201, twin


# ---------- no enumeration ----------


async def test_sign_in_answers_the_same_for_an_unknown_user_and_a_wrong_password(_api, _alice):
    wrong = await _signin(_api, "alice", "not-the-password")
    unknown = await _signin(_api, "nobody-here", "not-the-password")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.headers["www-authenticate"] == unknown.headers["www-authenticate"]
    assert set(wrong.headers) == set(unknown.headers) - {"x-nothing"}


async def test_sign_in_hashes_even_when_the_user_does_not_exist(_api, monkeypatch):
    """The reason the answers take equally long: the same PBKDF2 work happens either way."""
    calls = []
    real = auth_module.hash_password
    monkeypatch.setattr(
        auth_module, "hash_password", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    await _signin(_api, "nobody-here", "whatever-pass")
    assert calls == [1]


async def test_sign_in_and_sign_up_do_not_echo_which_part_was_wrong(_api, _alice):
    texts = {
        (await _signin(_api, "alice", "wrongwrong1")).text,
        (await _signin(_api, "ALICE", PASSWORD)).text,
        (await _signin(_api, "alice ", PASSWORD)).text,
    }
    assert len(texts) == 1
    assert "alice" not in texts.pop().lower()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ACCEPTED RISK, shown so the owner decides: sign-up answers 409 'Username already taken', "
        "so anyone can test whether a name exists. Normal for open sign-up; the nginx 5-per-minute "
        "limit slows it down. The only fix is not telling (needs e-mail confirmation)."
    ),
)
async def test_sign_up_does_not_reveal_that_a_name_exists(_api, _alice):
    taken = await _signup(_api, "alice")
    fresh = await _signup(_api, "someone-new")
    assert taken.status_code == fresh.status_code


# ---------- passwords stay secret ----------


async def test_the_password_is_never_in_a_response(_api):
    secret = "UniquePass-9f3a7c1e"  # gitleaks:allow (fake test value)
    texts = [
        (await _signup(_api, "erin", secret)).text,
        (await _signin(_api, "erin", secret)).text,
        (await _signin(_api, "erin", secret + "x")).text,
        (await _signup(_api, "erin", secret)).text,  # duplicate
        (await _signup(_api, "erin2", secret[:5])).text,  # too short: 422 must not echo it
        (await _api.post(f"{API}/tokens", json={"username": "erin", "password": [secret]})).text,
        (await _api.post(f"{API}/annotators", json={"username": "x", "password": 5})).text,
    ]
    for text in texts:
        assert secret not in text and secret[:5] not in text


async def test_the_password_and_token_are_never_logged(_api, caplog):
    secret = "UniquePass-9f3a7c1e"  # gitleaks:allow (fake test value)
    caplog.set_level(logging.DEBUG)
    up = await _signup(_api, "frank", secret)
    await _signin(_api, "frank", secret)
    await _signin(_api, "frank", "bad-password-1")
    await _signup(_api, "frank", secret)
    log = caplog.text
    assert secret not in log and "bad-password-1" not in log
    assert up.json()["access_token"] not in log


async def test_the_stored_hash_is_salted_slow_and_not_the_password(_api):
    secret = "UniquePass-9f3a7c1e"  # gitleaks:allow (fake test value)
    await _signup(_api, "gina", secret)
    await _signup(_api, "hank", secret)
    gina, hank = await _stored("gina"), await _stored("hank")
    for row in (gina, hank):
        assert row.password_hash != secret and secret not in row.password_hash
        assert len(row.password_hash) == 64 and int(row.password_hash, 16) >= 0  # SHA-256 hex
        assert row.password_salt.startswith("600000$")  # OWASP's floor for PBKDF2-SHA256
    assert gina.password_hash != hank.password_hash  # same password, different salt
    assert gina.password_salt != hank.password_salt
    assert auth_module.verify_password(secret, gina.password_hash, gina.password_salt)
    assert not auth_module.verify_password("x" + secret, gina.password_hash, gina.password_salt)


async def test_the_hash_does_not_reveal_a_common_password(_api):
    """Not a bare SHA-256/MD5 of the text, so a lookup table of common hashes does not work."""
    import hashlib

    await _signup(_api, "ivan", "password123")
    row = await _stored("ivan")
    for digest in (hashlib.sha256, hashlib.md5, hashlib.sha1):
        assert row.password_hash != digest(b"password123").hexdigest()
