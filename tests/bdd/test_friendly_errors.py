import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from pytest_bdd import parsers, scenarios, then, when

scenarios("../../docs/behaviours/friendly-errors.feature")

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
STATUSES = [400, 401, 403, 404, 409, 422, 429, 500, 503]

_FAILURES = {
    "no connection": "network",
    "status 400 or 422": [400, 422],
    "status 401": [401],
    "status 403": [403],
    "status 404": [404],
    "status 409": [409],
    "status 429": [429],
    "status 503": [503],
    "any other status": [500],
}


@pytest.fixture(scope="session")
def _error_keys(tmp_path_factory):
    """The real errors.ts, bundled with the esbuild the frontend already has and run in node.

    Returns {"network": key for a TypeError, status: key for an ApiError with that status}.
    """
    esbuild = FRONTEND / "node_modules" / ".bin" / "esbuild"
    if not esbuild.exists() or not shutil.which("node"):
        pytest.skip("needs node and the frontend dependencies (npm ci in frontend/)")
    bundle = tmp_path_factory.mktemp("errors") / "errors.mjs"
    subprocess.run(
        [esbuild, "src/api/errors.ts", "--format=esm", f"--outfile={bundle}", "--log-level=error"],
        cwd=FRONTEND,
        check=True,
    )
    script = (
        f"import {{ApiError, errorKey}} from {json.dumps(bundle.as_uri())};"
        f"const out = {{network: errorKey(new TypeError('Failed to fetch'))}};"
        f"for (const s of {json.dumps(STATUSES)}) out[s] = errorKey(new ApiError('x', s));"
        "console.log(JSON.stringify(out));"
    )
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def _arabic() -> dict[str, str]:
    text = (FRONTEND / "src" / "i18n" / "translations" / "ar.ts").read_text(encoding="utf-8")
    return dict(re.findall(r"'(error\.\w+)':\s*'(.*)',", text))


@when(parsers.parse("a request fails with {failure}"))
def _request_fails(_ctx, _error_keys, failure):
    cause = _FAILURES[failure]
    _ctx["keys"] = (
        [_error_keys["network"]]
        if cause == "network"
        else [_error_keys[str(status)] for status in cause]
    )


@then(parsers.parse('the page shows the message for "{key}"'))
def _message_for(_ctx, _pytest_bdd_example, key):
    assert _ctx["keys"] == [key] * len(_ctx["keys"])
    assert _arabic()[key] == _pytest_bdd_example["message"]


@then(
    "the keys error.usernameShort, error.passwordShort and error.passwordMismatch exist in Arabic"
)
def _sign_up_messages():
    arabic = _arabic()
    for key in ("error.usernameShort", "error.passwordShort", "error.passwordMismatch"):
        assert arabic.get(key)


@when("any API route fails")
def _routes_fail(_api):
    _api.many(
        [
            ("GET", "/api/v1/assignments", {}),
            ("GET", "/api/v1/hadiths/999", {}),
            ("POST", "/api/v1/annotators", {"json": {"username": "ab", "password": "1"}}),
        ]
    )


@then("headers set on the exception (for example Retry-After, WWW-Authenticate) are kept")
def _exception_headers_kept(_ctx):
    assert _ctx["responses"][0].headers["www-authenticate"] == "Bearer"


@then(
    'a validation failure has detail "Request validation failed" and an errors list with a field name'
)
def _validation_body(_ctx):
    body = _ctx["responses"][2].json()
    assert body["detail"] == "Request validation failed"
    assert all(e["field"] for e in body["errors"]) and body["errors"]
