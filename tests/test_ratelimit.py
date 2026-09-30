import pytest
from httpx import ASGITransport, AsyncClient

from features import Features
from main import create_app

API = "/api/v1"
CREDS = {"username": "alice", "password": "wrong-pass-1"}


@pytest.fixture
def _limits_on(monkeypatch):
    from ratelimit import limiter

    limiter.enabled = True
    limiter.reset()
    monkeypatch.setenv("RATE_LIMIT_AUTH", "3/minute")
    monkeypatch.setenv("RATE_LIMIT_DEFAULT", "5/minute")


async def test_sign_in_is_limited_and_says_when_to_retry(_client, _limits_on):
    codes = [(await _client.post(f"{API}/tokens", json=CREDS)).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    res = await _client.post(f"{API}/tokens", json=CREDS)
    assert res.headers["content-type"] == "application/problem+json"
    assert res.headers["retry-after"] == "60"
    assert res.json()["status"] == 429 and "Rate limit exceeded" in res.json()["detail"]


async def test_sign_up_is_limited_too(_client, _limits_on):
    for i in range(3):
        body = {"username": f"user{i}", "password": "secret123"}
        assert (await _client.post(f"{API}/annotators", json=body)).status_code == 201
    over = await _client.post(
        f"{API}/annotators", json={"username": "user9", "password": "secret123"}
    )
    assert over.status_code == 429


async def test_other_routes_get_the_default_limit_with_headers(_limits_on):
    app = create_app(Features(annotation=False, kv_pairs=False, benchmark=False, search=False))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        first = await c.get(f"{API}")
        codes = [(await c.get(f"{API}")).status_code for _ in range(5)]
    assert first.headers["x-ratelimit-limit"] == "5"
    assert codes[-1] == 429 and codes[0] == 200


async def test_limiter_can_be_disabled(_client):
    codes = {(await _client.post(f"{API}/tokens", json=CREDS)).status_code for _ in range(15)}
    assert codes == {401}


async def test_static_files_are_not_limited(tmp_path, _limits_on):
    static = tmp_path / "s"
    static.mkdir()
    (static / "index.html").write_text("spa")
    app = create_app(
        Features(annotation=False, kv_pairs=False, benchmark=False, search=False),
        static_dir=str(static),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        codes = {(await c.get("/some/page")).status_code for _ in range(10)}
    assert codes == {200}
