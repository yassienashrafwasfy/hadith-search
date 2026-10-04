"""Plain helpers shared by the security tests (fixtures live in conftest.py)."""

import re

PASSWORD = "correct-horse-1"
API = "/api/v1"
# Text that must never reach a client: Python traces, driver and ORM names, SQL, test secrets.
LEAKS = re.compile(
    r"Traceback|File \"|psycopg|sqlalchemy|asyncpg|pgvector|postgres|SELECT |INSERT |UPDATE |"
    r"DELETE FROM|syntax error|test-only-password|test-secret",
    re.IGNORECASE,
)


async def _sign_up(api, username: str, password: str = PASSWORD) -> dict:
    res = await api.post(f"{API}/annotators", json={"username": username, "password": password})
    assert res.status_code == 201, res.text
    body = res.json()
    return {
        "id": body["annotator"]["id"],
        "username": username,
        "headers": {"Authorization": f"Bearer {body['access_token']}"},
        "queries": [a["query_id"] for a in body["annotator"]["assignments"]],
    }


def assert_clean(res, *, allow=range(200, 500), echoed: str = "") -> None:
    """The answer is not a 5xx, a failure is problem+json, and nothing internal shows in it.

    `echoed` is the request text the API may quote back (an unknown method name, say); it is
    removed before looking for leaks, so a payload that contains 'SELECT ' does not count.
    """
    assert res.status_code in allow, f"{res.request.method} {res.request.url}: {res.status_code}"
    if res.status_code >= 400:
        assert res.headers["content-type"].startswith("application/problem+json"), res.text[:200]
    text = res.text.replace(echoed, "") if echoed else res.text
    assert not LEAKS.search(text), res.text[:300]
