"""tools/pick-runner.sh against a local fake of the GitHub runners API."""

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "pick-runner.sh"
HOSTED = ["ubuntu-latest"]
SELF = ["self-hosted", "hadith-ci"]


def _runner(status="online", busy=False, label="hadith-ci"):
    return {"status": status, "busy": busy, "labels": [{"name": "self-hosted"}, {"name": label}]}


@pytest.fixture
def _api():
    """Fake API: set `api.status` / `api.payload`; `api.seen` records request headers and paths."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            server.seen.append((self.path, self.headers.get("Authorization")))
            body = server.payload if isinstance(server.payload, bytes) else b""
            if not isinstance(server.payload, bytes):
                body = json.dumps(server.payload).encode()
            self.send_response(server.status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.status, server.payload, server.seen = 200, {"runners": []}, []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server.url = f"http://127.0.0.1:{server.server_port}"
    yield server
    server.shutdown()
    server.server_close()


def _run(tmp_path, api, **env):
    out, summary = tmp_path / "out", tmp_path / "summary"
    full = {
        "PATH": os.environ["PATH"],
        "EVENT_NAME": "push",
        "REPOSITORY": "o/r",
        "RUNNER_STATUS_TOKEN": "tok",
        "API_URL": api.url,
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(summary),
        **env,
    }
    full = {k: v for k, v in full.items() if v is not None}
    done = subprocess.run(["bash", str(SCRIPT)], env=full, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    lines = dict(line.split("=", 1) for line in out.read_text().splitlines())
    return json.loads(lines["runner"]), lines["self_hosted"], summary.read_text()


def test_idle_runner_is_used(tmp_path, _api):
    _api.payload = {"runners": [_runner()]}
    assert _run(tmp_path, _api)[:2] == (SELF, "true")
    assert _api.seen[0] == ("/repos/o/r/actions/runners?per_page=100", "Bearer tok")


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_other_trusted_events_use_it(tmp_path, _api, event):
    _api.payload = {"runners": [_runner()]}
    assert _run(tmp_path, _api, EVENT_NAME=event)[0] == SELF


def test_same_repo_pull_request_is_trusted(tmp_path, _api):
    _api.payload = {"runners": [_runner()]}
    assert _run(tmp_path, _api, EVENT_NAME="pull_request", HEAD_REPO="o/r")[0] == SELF


@pytest.mark.parametrize("head", ["someone/r", ""])
def test_fork_pull_request_never_uses_it_and_skips_the_api(tmp_path, _api, head):
    _api.payload = {"runners": [_runner()]}
    runner, flag, _ = _run(tmp_path, _api, EVENT_NAME="pull_request", HEAD_REPO=head)
    assert (runner, flag) == (HOSTED, "false") and _api.seen == []


def test_untrusted_event_falls_back(tmp_path, _api):
    assert _run(tmp_path, _api, EVENT_NAME="pull_request_target")[0] == HOSTED
    assert _api.seen == []


@pytest.mark.parametrize(
    "runners, word",
    [
        ([_runner(busy=True)], "busy"),
        ([_runner(status="offline")], "offline"),
        ([], "registered"),
        ([_runner(label="other")], "registered"),
    ],
)
def test_unavailable_runner_falls_back_with_reason(tmp_path, _api, runners, word):
    _api.payload = {"runners": runners}
    runner, flag, summary = _run(tmp_path, _api)
    assert (runner, flag) == (HOSTED, "false") and word in summary


def test_one_idle_among_busy_ones_is_used(tmp_path, _api):
    _api.payload = {"runners": [_runner(busy=True), _runner()]}
    assert _run(tmp_path, _api)[0] == SELF


def test_missing_token_falls_back_without_calling_the_api(tmp_path, _api):
    runner, _, summary = _run(tmp_path, _api, RUNNER_STATUS_TOKEN="")
    assert runner == HOSTED and "RUNNER_STATUS_TOKEN" in summary and _api.seen == []


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_api_errors_fall_back(tmp_path, _api, status):
    _api.status = status
    runner, _, summary = _run(tmp_path, _api)
    assert runner == HOSTED and str(status) in summary


def test_garbage_answer_falls_back(tmp_path, _api):
    _api.payload = b"<html>not json"
    assert _run(tmp_path, _api)[0] == HOSTED


def test_unreachable_api_falls_back(tmp_path, _api):
    _api.shutdown()
    _api.server_close()
    assert _run(tmp_path, _api)[0] == HOSTED
