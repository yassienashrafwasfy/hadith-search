import json

import pytest

# ---------- auth ----------


async def test_signup_assigns_queries_and_returns_token(_client):
    res = await _client.post("/auth/signup", json={"username": "alice", "password": "secret123"})
    body = res.json()
    assert res.status_code == 200
    assert body["annotator"]["username"] == "alice"
    assert [a["query_id"] for a in body["assignments"]] == ["q1", "q2"]  # QUERIES_PER_ANNOTATOR = 2


@pytest.mark.parametrize(
    "user,pw,msg", [("ab", "secret123", "Username"), ("alice", "123", "Password")]
)
async def test_signup_validation(_client, user, pw, msg):
    res = await _client.post("/auth/signup", json={"username": user, "password": pw})
    assert res.status_code == 400 and msg in res.json()["detail"]


async def test_signup_duplicate_username(_client, _auth_headers):
    res = await _client.post("/auth/signup", json={"username": "alice", "password": "secret123"})
    assert res.status_code == 409


async def test_signin_and_me(_client, _auth_headers):
    ok = await _client.post("/auth/signin", json={"username": "alice", "password": "secret123"})
    assert ok.status_code == 200
    me = await _client.get("/auth/me", headers={"Authorization": f"Bearer {ok.json()['token']}"})
    assert me.json()["annotator"]["username"] == "alice"
    bad = await _client.post("/auth/signin", json={"username": "alice", "password": "wrong-pass"})
    assert bad.status_code == 401


async def test_signout_invalidates_token(_client, _auth_headers):
    assert (await _client.post("/auth/signout", headers=_auth_headers)).json() == {"success": True}
    assert (await _client.get("/auth/me", headers=_auth_headers)).status_code == 401


async def test_bad_auth_header(_client):
    assert (await _client.get("/auth/me", headers={"Authorization": "Token x"})).status_code == 401
    assert (await _client.get("/auth/me")).status_code == 422  # header is required


async def test_hash_password_roundtrip():
    from routers.auth import hash_password, verify_password

    h, salt = hash_password("pw")
    assert verify_password("pw", h, salt)
    assert not verify_password("other", h, salt)


async def test_assignment_cap_per_query(_client):
    """ANNOTATORS_PER_QUERY = 3: the 4th signup can't get a query already assigned to 3 people."""
    for i in range(3):
        (await _client.post("/auth/signup", json={"username": f"user{i}", "password": "secret123"}))
    res = await _client.post("/auth/signup", json={"username": "user3", "password": "secret123"})
    assert len(res.json()["assignments"]) == 2


# ---------- annotation ----------


async def test_annotation_queries_listing(_client, _auth_headers):
    res = (await _client.get("/annotation/queries", headers=_auth_headers)).json()["queries"]
    assert [(q["query_id"], q["total"], q["graded"]) for q in res] == [("q1", 2, 0), ("q2", 2, 0)]


async def test_current_state_includes_hadith_text(_client, _auth_headers):
    res = (await _client.get("/annotation/q1/current", headers=_auth_headers)).json()
    assert res["total"] == 2
    assert res["pooled_hadiths"][0]["english_hadith"] == "prayer is the pillar of faith"


async def test_current_state_forbidden_when_unassigned(_client, _auth_headers):
    assert (await _client.get("/annotation/q3/current", headers=_auth_headers)).status_code == 403


async def test_save_label_advances_progress(_client, _auth_headers):
    res = await _client.post(
        "/annotation/q1/label", json={"hadith_id": 1, "index": 0, "label": 2}, headers=_auth_headers
    )
    assert res.json() == {"success": True, "current_index": 1}
    state = (await _client.get("/annotation/q1/current", headers=_auth_headers)).json()
    assert state["labels"] == {"1": 2} and state["current_index"] == 1


async def test_save_label_upserts(_client, _auth_headers):
    for label in (0, 2):
        (
            await _client.post(
                "/annotation/q1/label",
                json={"hadith_id": 1, "index": 0, "label": label},
                headers=_auth_headers,
            )
        )
    assert (await _client.get("/annotation/q1/current", headers=_auth_headers)).json()[
        "labels"
    ] == {"1": 2}


async def test_save_label_rejects_invalid_label(_client, _auth_headers):
    res = await _client.post(
        "/annotation/q1/label", json={"hadith_id": 1, "index": 0, "label": 5}, headers=_auth_headers
    )
    assert res.status_code == 400


async def test_navigate(_client, _auth_headers):
    assert (await _client.post("/annotation/q1/navigate?index=1", headers=_auth_headers)).json()[
        "current_index"
    ] == 1
    assert (
        await _client.post("/annotation/q1/navigate?index=9", headers=_auth_headers)
    ).status_code == 400


async def test_agreement_stats_shape(_client, _auth_headers):
    res = await _client.get("/annotation/stats/agreement", headers=_auth_headers)
    assert res.status_code == 200
    assert set(res.json()) == {"per_query", "overall"}


# ---------- kv pairs ----------


async def test_kv_list_filters_and_paginates(_client, _kv_rows):
    assert (await _client.get("/kv-pairs")).json()["total"] == 3
    assert (await _client.get("/kv-pairs?status=verified")).json()["total"] == 1
    assert (await _client.get("/kv-pairs?topic=prayer")).json()["total"] == 2
    page = (await _client.get("/kv-pairs?limit=1&offset=1")).json()
    assert [p["id"] for p in page["pairs"]] == [2]


async def test_kv_stats(_client, _kv_rows):
    body = (await _client.get("/kv-pairs/stats")).json()
    assert body["by_status"] == {"pending": 2, "verified": 1}
    assert body["by_topic"] == {"prayer": 2, "fasting": 1}


async def test_kv_verify_single(_client, _kv_rows):
    assert (await _client.post("/kv-pairs/1/verify", json={"status": "verified"})).json() == {
        "id": 1,
        "status": "verified",
    }
    assert "error" in (await _client.post("/kv-pairs/1/verify", json={"status": "maybe"})).json()
    assert (
        "error" in (await _client.post("/kv-pairs/99/verify", json={"status": "verified"})).json()
    )


async def test_kv_verify_batch_and_export(_client, _kv_rows):
    res = await _client.post(
        "/kv-pairs/verify-batch",
        json=[
            {"id": 1, "status": "rejected"},
            {"id": 2, "status": "verified"},
            {"id": 3, "status": "bogus"},
            {},
        ],
    )
    assert res.json() == {"updated": 2}
    export = (await _client.get("/kv-pairs/export")).json()
    assert [p["id"] for p in export["pairs"]] == [2, 3]  # id 3 was already verified


# ---------- benchmark ----------


async def test_benchmark_stats_missing_file(_client):
    assert "error" in (await _client.get("/benchmark/stats")).json()


async def test_benchmark_results_reads_json(_client, _patched_paths):
    (_patched_paths / "qrels_results_path.json").write_text(json.dumps({"ok": 1}))
    assert (await _client.get("/benchmark/results")).json() == {"ok": 1}
