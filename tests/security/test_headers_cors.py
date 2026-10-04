"""Security headers and CORS: only what the app and the nginx config really do.

Gaps (things not set at all) are listed as xfail so they show up and flip when fixed.
"""

import re
from pathlib import Path

import pytest

from main import CONTENT_SECURITY_POLICY, DOCS_PATHS, SECURITY_HEADERS
from security._helpers import API, PASSWORD

NGINX = Path(__file__).resolve().parents[2] / "nginx"
GOOD_ORIGIN = "https://hadith.example"
_PROD = {"app_env": "prod", "auth_secret": "p" * 40, "cors_origins": GOOD_ORIGIN}


# ---------- headers the app sets ----------


async def _answers(api, alice):
    """One answer of every kind: success, redirect-less pages, and each client error."""
    return {
        "api 200": await api.get(API),
        "authenticated 200": await api.get(f"{API}/assignments", headers=alice["headers"]),
        "search 200": await api.get(f"{API}/searches", params={"q": "prayer", "method": "bm25"}),
        "404": await api.get(f"{API}/nothing"),
        "401": await api.get(f"{API}/assignments"),
        "422": await api.get(f"{API}/hadiths/abc"),
        "405": await api.delete(f"{API}/tokens"),
        "frontend page": await api.get("/"),
        "frontend route": await api.get("/some/page"),
        "preflight": await api.options(
            f"{API}/tokens",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
        ),
    }


async def test_every_kind_of_answer_carries_the_security_headers(_api, _alice):
    for name, res in (await _answers(_api, _alice)).items():
        for header, value in SECURITY_HEADERS.items():
            assert res.headers.get(header) == value, (name, header)
        assert res.headers["content-security-policy"] == CONTENT_SECURITY_POLICY, name


def test_the_policy_is_as_strict_as_the_notes_say():
    csp = dict(part.strip().split(" ", 1) for part in CONTENT_SECURITY_POLICY.split(";"))
    assert csp["default-src"] == "'self'" and csp["script-src"] == "'self'"
    assert csp["object-src"] == "'none'" and csp["frame-ancestors"] == "'none'"
    assert csp["base-uri"] == "'self'" and csp["connect-src"] == "'self'"
    assert "'unsafe-eval'" not in CONTENT_SECURITY_POLICY
    assert "script-src 'self' " not in CONTENT_SECURITY_POLICY  # no extra script hosts
    assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"
    assert SECURITY_HEADERS["Referrer-Policy"] == "no-referrer"


async def test_the_docs_pages_are_the_only_ones_without_the_policy(_make_app, _open):
    async with _open(_make_app(app_env="dev")) as api:
        for path in DOCS_PATHS:
            res = await api.get(path)
            assert "content-security-policy" not in res.headers
            assert res.headers["x-frame-options"] == "DENY"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "FINDING (low): a 500 is produced by Starlette's outer error middleware, outside the "
        "app's header middleware, so it carries no security headers (the body is plain text, so "
        "the impact is small). Suggested fix: handle Exception in backend/rest.py so the answer "
        "goes through the normal stack, or add the headers in nginx with `always`."
    ),
)
async def test_a_500_carries_the_security_headers_too(_api, monkeypatch):
    import routers.hadiths as hadiths_module

    monkeypatch.setattr(hadiths_module, "get_hadith_row", lambda _id: 1 / 0)
    res = await _api.get(f"{API}/hadiths/1")
    assert res.status_code == 500 and res.headers["x-content-type-options"] == "nosniff"


async def test_private_data_is_cached_privately_and_public_data_publicly(_api, _alice):
    mine = await _api.get(f"{API}/assignments", headers=_alice["headers"])
    assert mine.headers["cache-control"].startswith("private")
    assert (
        (await _api.get(f"{API}/annotators/me", headers=_alice["headers"]))
        .headers["cache-control"]
        .startswith("private")
    )
    search = await _api.get(f"{API}/searches", params={"q": "prayer", "method": "bm25"})
    assert search.headers["cache-control"].startswith("public")


async def test_a_login_answer_is_not_cached_by_the_server_response_rules(_api):
    """A POST answer is not stored by caches unless told so; make sure nothing tells them to."""
    res = await _api.post(f"{API}/annotators", json={"username": "cacheuser", "password": PASSWORD})
    assert "public" not in res.headers.get("cache-control", "")
    assert "etag" not in res.headers


@pytest.mark.xfail(
    strict=True,
    reason=(
        "GAP (low): answers that carry a bearer token (POST /tokens, POST /annotators) have no "
        "'Cache-Control: no-store'. POST answers are not reused by caches by default, so this is "
        "belt and braces. Suggested fix: add the header in backend/routers/auth.py."
    ),
)
async def test_token_answers_say_no_store(_api, _alice):
    res = await _api.post(f"{API}/tokens", json={"username": "alice", "password": PASSWORD})
    assert res.status_code == 201 and "no-store" in res.headers.get("cache-control", "")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "GAP, DECISION NEEDED: neither the app nor nginx/default.conf sends Strict-Transport-"
        "Security. If a TLS terminator in front of nginx adds it, ignore this; if not, add "
        '`add_header Strict-Transport-Security "max-age=31536000" always;` where TLS ends. '
        "Not set in code today, so it is not invented here."
    ),
)
async def test_hsts_is_sent(_api):
    assert "strict-transport-security" in (await _api.get(API)).headers


# ---------- CORS ----------


async def _preflight(api, origin, method="POST", headers="authorization,content-type"):
    return await api.options(
        f"{API}/tokens",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )


async def test_prod_cors_allows_only_the_listed_origin(_make_app, _open):
    async with _open(_make_app(**_PROD)) as api:
        ok = await _preflight(api, GOOD_ORIGIN)
        assert ok.status_code == 200
        assert ok.headers["access-control-allow-origin"] == GOOD_ORIGIN
        for evil in (
            "https://evil.example",
            "http://hadith.example",  # same host, other scheme
            "https://hadith.example.evil.com",
            "https://evilhadith.example",
            "https://hadith.example:8443",
            "null",
            "file://",
        ):
            res = await _preflight(api, evil)
            assert res.status_code == 400, evil
            assert "access-control-allow-origin" not in res.headers, evil
            simple = await api.get(API, headers={"Origin": evil})
            assert "access-control-allow-origin" not in simple.headers, evil


async def test_prod_cors_never_allows_credentials(_make_app, _open):
    async with _open(_make_app(**_PROD)) as api:
        for res in (
            await _preflight(api, GOOD_ORIGIN),
            await api.get(API, headers={"Origin": GOOD_ORIGIN}),
        ):
            assert "access-control-allow-credentials" not in res.headers
            assert res.headers["access-control-allow-origin"] != "*"


async def test_cors_allows_only_the_methods_and_headers_the_app_uses(_make_app, _open):
    async with _open(_make_app(**_PROD)) as api:
        ok = await _preflight(api, GOOD_ORIGIN, method="PATCH", headers="authorization")
        assert ok.status_code == 200
        allowed = set(ok.headers["access-control-allow-methods"].split(", "))
        assert allowed == {"GET", "POST", "PUT", "PATCH", "DELETE"}
        assert "TRACE" not in allowed and "CONNECT" not in allowed
        for method in ("TRACE", "CONNECT"):
            assert (await _preflight(api, GOOD_ORIGIN, method=method)).status_code == 400
        for header in ("x-forwarded-host", "x-http-method-override", "cookie"):
            assert (await _preflight(api, GOOD_ORIGIN, headers=header)).status_code == 400


async def test_the_exposed_headers_do_not_include_anything_sensitive(_make_app, _open):
    async with _open(_make_app(**_PROD)) as api:
        res = await api.get(API, headers={"Origin": GOOD_ORIGIN})
    exposed = {h.strip().lower() for h in res.headers["access-control-expose-headers"].split(",")}
    assert exposed == {"etag", "location", "server-timing", "www-authenticate"}


async def test_a_list_of_origins_is_split_and_trimmed(_make_app, _open):
    both = f"{GOOD_ORIGIN}, https://second.example"
    async with _open(_make_app(**{**_PROD, "cors_origins": both})) as api:
        for origin in (GOOD_ORIGIN, "https://second.example"):
            assert (await _preflight(api, origin)).status_code == 200
        assert (await _preflight(api, "https://third.example")).status_code == 400


async def test_dev_without_cors_origins_allows_only_the_local_vite_origins(_make_app, _open):
    async with _open(_make_app(app_env="dev")) as api:
        assert (await _preflight(api, "http://localhost:5173")).status_code == 200
        assert (await _preflight(api, "https://evil.example")).status_code == 400


def test_prod_refuses_a_wildcard_cors_origin():
    from pydantic import ValidationError

    from settings import Settings

    with pytest.raises(ValidationError):
        Settings(**{**_PROD, "cors_origins": "*"})


# ---------- nginx (read from the config file, as tools/test-nginx.sh runs it in Docker) ----------


def _conf() -> str:
    return re.sub(r"#.*", "", (NGINX / "default.conf").read_text())


def test_nginx_hides_its_version_and_caps_bodies():
    conf = _conf()
    assert re.search(r"^\s*server_tokens\s+off;", conf, re.M)
    assert re.search(r"client_max_body_size\s+1m;", conf)


def test_nginx_limits_sign_in_and_sign_up_hard_and_search_softly():
    conf = _conf()
    assert "limit_req_zone $binary_remote_addr zone=auth:10m rate=1r/m;" in conf
    for route in ("tokens", "annotators"):
        block = conf[conf.index(f"location = /api/v1/{route}") :].split("}")[0]
        assert "limit_req zone=auth burst=4 nodelay;" in block
        assert "limit_req_status 429;" in block
    block = conf[conf.index("location = /api/v1/searches") :].split("}")[0]
    assert "limit_req zone=search" in block and "limit_conn search_conn" in block
    assert "limit_req_status 503;" in block and "limit_conn_status 503;" in block


def test_nginx_does_not_trust_a_client_supplied_forwarded_for():
    """The limit counts per address; if the client could add to X-Forwarded-For it could dodge it."""
    proxy = (NGINX / "proxy_app.conf").read_text()
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in proxy
    assert "$proxy_add_x_forwarded_for" not in proxy
    assert "proxy_set_header X-Real-IP $remote_addr;" in proxy


def test_nginx_gives_its_own_429_and_503_the_problem_json_shape():
    conf = _conf()
    assert conf.count("application/problem+json") == 2
    assert "Retry-After 60" in conf and "Retry-After 5" in conf
