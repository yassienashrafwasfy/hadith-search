import json

import pytest

API = "/api/v1"
CREDS = {"username": "alice", "password": "secret123"}

# ---------- annotators and tokens ----------


async def test_signup_creates_annotator_and_returns_token(_client):
    res = await _client.post(f"{API}/annotators", json=CREDS)
    body = res.json()
    assert res.status_code == 201
    assert res.headers["Location"] == f"{API}/annotators/{body['annotator']['id']}"
    assert body["token_type"] == "Bearer" and body["expires_in"] == 3600
    assert body["annotator"]["username"] == "alice"
    # QUERIES_PER_ANNOTATOR = 2
    assert [a["query_id"] for a in body["annotator"]["assignments"]] == ["q1", "q2"]


@pytest.mark.parametrize(
    "user,pw,field", [("ab", "secret123", "username"), ("alice", "123", "password")]
)
async def test_signup_validation_is_422(_client, user, pw, field):
    res = await _client.post(f"{API}/annotators", json={"username": user, "password": pw})
    assert res.status_code == 422
    assert res.headers["content-type"] == "application/problem+json"
    assert [e["field"] for e in res.json()["errors"]] == [field]


async def test_signup_duplicate_username_is_409(_client, _auth_headers):
    res = await _client.post(f"{API}/annotators", json=CREDS)
    assert res.status_code == 409 and res.json()["title"] == "Conflict"


async def test_create_token_and_read_profile(_client, _auth_headers):
    ok = await _client.post(f"{API}/tokens", json=CREDS)
    assert ok.status_code == 201
    headers = {"Authorization": f"Bearer {ok.json()['access_token']}"}
    me = await _client.get(f"{API}/annotators/me", headers=headers)
    assert me.json()["username"] == "alice"
    assert me.json()["_links"]["assignments"]["href"] == f"{API}/assignments"
    bad = await _client.post(f"{API}/tokens", json={"username": "alice", "password": "wrong-pass"})
    assert bad.status_code == 401
    assert bad.headers["www-authenticate"] == "Bearer"


async def test_annotator_by_id_is_self_only(_client, _auth_headers):
    me = (await _client.get(f"{API}/annotators/me", headers=_auth_headers)).json()
    own = await _client.get(f"{API}/annotators/{me['id']}", headers=_auth_headers)
    assert own.status_code == 200
    other = await _client.get(f"{API}/annotators/{me['id'] + 1}", headers=_auth_headers)
    assert other.status_code == 403


@pytest.mark.parametrize(
    "headers", [{"Authorization": "Token x"}, {"Authorization": "Bearer "}, {}]
)
async def test_missing_or_malformed_credentials_are_401(_client, headers):
    res = await _client.get(f"{API}/annotators/me", headers=headers)
    assert res.status_code == 401 and res.headers["www-authenticate"] == "Bearer"


async def test_forged_and_expired_tokens_are_rejected(_client):
    import jwt
    from conftest import TEST_SECRET

    forged = jwt.encode(
        {"sub": "1", "name": "x", "exp": 9999999999}, "other-" * 8, algorithm="HS256"
    )
    expired = jwt.encode({"sub": "1", "name": "x", "exp": 1}, TEST_SECRET, algorithm="HS256")
    for token in (forged, expired, "not-a-jwt"):
        res = await _client.get(
            f"{API}/annotators/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert res.status_code == 401


async def test_hash_password_roundtrip():
    from routers.auth import hash_password, verify_password

    h, salt = hash_password("pw")
    assert verify_password("pw", h, salt)
    assert not verify_password("other", h, salt)


async def test_assignment_cap_per_query(_client):
    """ANNOTATORS_PER_QUERY = 3: the 4th signup can't get a query already assigned to 3 people."""
    for i in range(3):
        await _client.post(
            f"{API}/annotators", json={"username": f"user{i}", "password": "secret123"}
        )
    res = await _client.post(
        f"{API}/annotators", json={"username": "user3", "password": "secret123"}
    )
    assert len(res.json()["annotator"]["assignments"]) == 2


# ---------- assignments, labels, progress ----------


async def test_assignment_listing(_client, _auth_headers):
    res = (await _client.get(f"{API}/assignments", headers=_auth_headers)).json()["assignments"]
    assert [(q["query_id"], q["total"], q["graded"]) for q in res] == [("q1", 2, 0), ("q2", 2, 0)]
    assert res[0]["_links"]["self"]["href"] == f"{API}/assignments/q1"


async def test_assignment_detail_includes_hadith_text_and_links(_client, _auth_headers):
    res = (await _client.get(f"{API}/assignments/q1", headers=_auth_headers)).json()
    assert res["total"] == 2
    assert res["pooled_hadiths"][0]["english_hadith"] == "prayer is the pillar of faith"
    assert res["_links"]["label"] == {
        "href": f"{API}/assignments/q1/labels/{{hadith_id}}",
        "templated": True,
    }


@pytest.mark.parametrize("query_id", ["q3", "nope"])
async def test_unassigned_or_unknown_assignment_is_404(_client, _auth_headers, query_id):
    res = await _client.get(f"{API}/assignments/{query_id}", headers=_auth_headers)
    assert res.status_code == 404


async def test_assignments_need_a_token(_client):
    assert (await _client.get(f"{API}/assignments")).status_code == 401


async def test_put_label_is_idempotent_and_reports_created_vs_replaced(_client, _auth_headers):
    url = f"{API}/assignments/q1/labels/1"
    first = await _client.put(url, json={"label": 0}, headers=_auth_headers)
    second = await _client.put(url, json={"label": 2}, headers=_auth_headers)
    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json()["label"] == 2
    state = (await _client.get(f"{API}/assignments/q1", headers=_auth_headers)).json()
    assert state["labels"] == {"1": 2}


async def test_put_label_rejects_invalid_label_and_foreign_hadith(_client, _auth_headers):
    bad_label = await _client.put(
        f"{API}/assignments/q1/labels/1", json={"label": 5}, headers=_auth_headers
    )
    assert bad_label.status_code == 422
    not_in_pool = await _client.put(
        f"{API}/assignments/q1/labels/2", json={"label": 1}, headers=_auth_headers
    )
    assert not_in_pool.status_code == 404


async def test_put_progress(_client, _auth_headers):
    url = f"{API}/assignments/q1/progress"
    ok = await _client.put(url, json={"index": 1}, headers=_auth_headers)
    assert ok.status_code == 200 and ok.json()["index"] == 1
    state = (await _client.get(f"{API}/assignments/q1", headers=_auth_headers)).json()
    assert state["current_index"] == 1
    for index in (9, -1):
        assert (
            await _client.put(url, json={"index": index}, headers=_auth_headers)
        ).status_code == 422


async def test_agreement_shape(_client, _auth_headers):
    res = await _client.get(f"{API}/agreement", headers=_auth_headers)
    assert res.status_code == 200
    assert set(res.json()) == {"per_query", "overall", "_links"}


# ---------- kv pairs ----------


async def test_kv_list_filters_and_paginates(_client, _kv_rows, _auth_headers):
    assert (await _client.get(f"{API}/kv-pairs", headers=_auth_headers)).json()["total"] == 3
    assert (await _client.get(f"{API}/kv-pairs?status=verified", headers=_auth_headers)).json()[
        "total"
    ] == 1
    assert (await _client.get(f"{API}/kv-pairs?topic=prayer", headers=_auth_headers)).json()[
        "total"
    ] == 2
    page = (await _client.get(f"{API}/kv-pairs?limit=1&offset=1", headers=_auth_headers)).json()
    assert [p["id"] for p in page["pairs"]] == [2]
    assert set(page["_links"]) == {"self", "first", "last", "prev", "next"}
    assert page["pairs"][0]["_links"]["self"]["href"] == f"{API}/kv-pairs/2"


async def test_kv_list_rejects_bad_paging(_client, _kv_rows, _auth_headers):
    for query in ("limit=0", "limit=1000", "offset=-1"):
        assert (
            await _client.get(f"{API}/kv-pairs?{query}", headers=_auth_headers)
        ).status_code == 422


async def test_kv_statistics(_client, _kv_rows, _auth_headers):
    body = (await _client.get(f"{API}/kv-pairs/statistics", headers=_auth_headers)).json()
    assert body["by_status"] == {"pending": 2, "verified": 1}
    assert body["by_topic"] == {"prayer": 2, "fasting": 1}


async def test_kv_patch_single(_client, _kv_rows, _auth_headers):
    ok = await _client.patch(
        f"{API}/kv-pairs/1", json={"status": "verified"}, headers=_auth_headers
    )
    assert ok.status_code == 200 and ok.json()["status"] == "verified"
    assert (
        await _client.patch(f"{API}/kv-pairs/1", json={"status": "maybe"}, headers=_auth_headers)
    ).status_code == 422
    missing = await _client.patch(
        f"{API}/kv-pairs/99", json={"status": "verified"}, headers=_auth_headers
    )
    assert missing.status_code == 404


async def test_kv_patch_batch_then_filter_verified(_client, _kv_rows, _auth_headers):
    res = await _client.patch(
        f"{API}/kv-pairs",
        json=[
            {"id": 1, "status": "rejected"},
            {"id": 2, "status": "verified"},
            {"id": 99, "status": "verified"},
        ],
        headers=_auth_headers,
    )
    assert res.json() == {"updated": 2}
    verified = (await _client.get(f"{API}/kv-pairs?status=verified", headers=_auth_headers)).json()
    assert [p["id"] for p in verified["pairs"]] == [2, 3]
    assert (
        await _client.patch(
            f"{API}/kv-pairs", json=[{"id": 1, "status": "bogus"}], headers=_auth_headers
        )
    ).status_code == 422


@pytest.mark.parametrize(
    "method,path", [("get", ""), ("get", "/statistics"), ("patch", "/1"), ("patch", "")]
)
async def test_kv_pairs_need_a_token(_client, _kv_rows, method, path):
    res = await getattr(_client, method)(f"{API}/kv-pairs{path}")
    assert res.status_code == 401


async def test_password_length_limits(_client):
    long = "x" * 129
    for body in (
        {"username": "alice", "password": long},
        {"username": "a" * 65, "password": "secret123"},
        {"username": "alice", "password": "short"},
    ):
        assert (await _client.post(f"{API}/annotators", json=body)).status_code == 422


async def test_unknown_user_and_wrong_password_look_the_same(_client, _auth_headers):
    unknown = await _client.post(
        f"{API}/tokens", json={"username": "nobody", "password": "secret123"}
    )
    wrong = await _client.post(
        f"{API}/tokens", json={"username": "alice", "password": "wrong-pass"}
    )
    assert (unknown.status_code, unknown.json()) == (wrong.status_code, wrong.json())


async def test_password_hashes_use_the_current_cost_and_verify_legacy_rows():
    import hashlib

    from routers.auth import PBKDF2_ITERATIONS, hash_password, verify_password

    digest, salt = hash_password("pw-secret")
    assert salt.startswith(f"{PBKDF2_ITERATIONS}$")
    assert verify_password("pw-secret", digest, salt)
    legacy = hashlib.pbkdf2_hmac("sha256", b"old-pass", b"abcd", 100_000).hex()
    assert verify_password("old-pass", legacy, "abcd")
    assert not verify_password("other", legacy, "abcd")


# ---------- benchmark and hadiths ----------


async def test_benchmark_missing_file_is_404(_client):
    for name in ("stats", "results", "comparison", "finetuned", "finetuned-stats"):
        assert (await _client.get(f"{API}/benchmark/{name}")).status_code == 404


async def test_benchmark_results_reads_json_and_is_cacheable(_client, _patched_paths):
    (_patched_paths / "qrels_results_path.json").write_text(json.dumps({"ok": 1}))
    res = await _client.get(f"{API}/benchmark/results")
    assert res.json() == {"ok": 1}
    assert res.headers["cache-control"] == "public, max-age=300"


async def test_benchmark_mode_cannot_escape_the_data_dir(_client):
    assert (await _client.get(f"{API}/benchmark/finetuned?mode=../x")).status_code == 422


async def test_benchmark_index_links(_client):
    links = (await _client.get(f"{API}/benchmark")).json()["_links"]
    assert links["stats"]["href"] == f"{API}/benchmark/stats"


async def test_hadith_resource(_client, _patched_paths):
    found = await _client.get(f"{API}/hadiths/1")
    assert found.json()["Book"] == "Bukhari"
    assert found.headers["cache-control"] == "public, max-age=3600"
    assert (await _client.get(f"{API}/hadiths/999")).status_code == 404
