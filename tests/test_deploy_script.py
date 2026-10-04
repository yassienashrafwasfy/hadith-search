"""tools/deploy.sh state machine, run against a fake `docker` so no containers are needed."""

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "deploy.sh"

FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$FAKE/log"
args="$*"
case "$args" in
  *"ps -q app-"*)
    colour=${args##*app-}
    [ -e "$FAKE/running-$colour" ] && echo "id-$colour" ;;
  inspect*)
    colour=${args##*id-}
    cat "$FAKE/health-$colour" 2>/dev/null || echo healthy ;;
  *"nginx -t"*) [ ! -e "$FAKE/reject" ] || exit 1 ;;
  *"exec -T nginx sh -c"*awk*)
    case "${args##* }" in
      */api/v1/health) cat "$FAKE/code-health" 2>/dev/null || echo 200 ;;
      */api/v1/searches*) cat "$FAKE/code-search" 2>/dev/null || echo 200 ;;
      *) echo 200 ;;
    esac ;;
  *"exec -T nginx sh -c"*"-O -"*)
    if [ -e "$FAKE/no-search" ]; then echo '{"_links":{"self":{}}}'; else echo '{"_links":{"searches":{}}}'; fi ;;
  "images --format"*) cut -d' ' -f1 "$FAKE/images" | grep "^${args##* }:" ;;
  "image inspect"*) grep "^${args##* } " "$FAKE/images" | cut -d' ' -f2 ;;
  "ps --format"*) cat "$FAKE/running-images" 2>/dev/null ;;
  "rmi "*) sed -i "\\|^${args#rmi } |d" "$FAKE/images" ;;
  *"up -d"*)
    for word in $args; do case "$word" in app-*) touch "$FAKE/running-${word#app-}" ;; esac; done ;;
  *" stop app-"*) rm -f "$FAKE/running-${args##*app-}" ;;
esac
exit 0
"""


@pytest.fixture
def _sandbox(tmp_path, monkeypatch):
    """A copy of the script in its own directory, with a fake docker first on PATH."""
    (tmp_path / "tools").mkdir()
    shutil.copy(SCRIPT, tmp_path / "tools" / "deploy.sh")
    fake = tmp_path / "fake"
    fake.mkdir()
    docker = tmp_path / "bin" / "docker"
    docker.parent.mkdir()
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{docker.parent}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE", str(fake))
    monkeypatch.setenv("CANARY_WATCH", "0")
    monkeypatch.setenv("HEALTH_TIMEOUT", "0")
    return tmp_path


def _run(sandbox, *args):
    return subprocess.run(
        ["bash", str(sandbox / "tools" / "deploy.sh"), *args],
        capture_output=True,
        text=True,
        cwd=sandbox,
    )


def _state(sandbox):
    lines = (sandbox / "deploy" / "state" / "state.env").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines)


def _routing(sandbox):
    return (sandbox / "deploy" / "state" / "routing.conf").read_text()


def _set_health(sandbox, colour, value):
    (sandbox / "fake" / f"health-{colour}").write_text(value)


@pytest.fixture
def _live(_sandbox):
    """Blue live, green deployed and healthy."""
    assert _run(_sandbox, "init").returncode == 0
    assert _run(_sandbox, "deploy", "registry/app:2").returncode == 0
    return _sandbox


def test_init_makes_blue_live(_sandbox):
    assert _run(_sandbox, "init").returncode == 0
    assert _state(_sandbox) == {"ACTIVE": "blue", "CANARY": "0", "PREVIOUS": ""}
    assert "* app-blue:8000;" in _routing(_sandbox)
    assert _run(_sandbox, "init").returncode != 0  # not twice


def test_commands_need_init(_sandbox):
    result = _run(_sandbox, "status")
    assert result.returncode != 0 and "init" in result.stderr


def test_canary_sends_a_share_to_the_idle_colour(_live):
    assert _run(_live, "canary", "20").returncode == 0
    assert _state(_live)["CANARY"] == "20"
    assert "20% app-green:8000;" in _routing(_live)
    assert "* app-blue:8000;" in _routing(_live)


@pytest.mark.parametrize("value", ["0", "100", "abc", ""])
def test_canary_percent_must_be_1_to_99(_live, value):
    assert _run(_live, "canary", value).returncode != 0
    assert _state(_live)["CANARY"] == "0"


def test_nothing_moves_to_an_unhealthy_colour(_live):
    _set_health(_live, "green", "unhealthy")
    assert _run(_live, "canary", "10").returncode != 0
    assert _run(_live, "promote").returncode != 0
    assert _state(_live) == {"ACTIVE": "blue", "CANARY": "0", "PREVIOUS": ""}


def test_canary_falls_back_when_the_colour_fails_while_watched(_live, monkeypatch):
    monkeypatch.setenv("CANARY_WATCH", "3")
    # healthy when the canary starts, unhealthy on the first check afterwards
    (_live / "fake" / "health-green").write_text("healthy")
    shim = _live / "bin" / "docker"
    shim.write_text(
        shim.read_text().replace(
            'cat "$FAKE/health-$colour" 2>/dev/null || echo healthy',
            'if [ -e "$FAKE/seen-$colour" ]; then echo unhealthy; else touch "$FAKE/seen-$colour"; '
            'cat "$FAKE/health-$colour"; fi',
        )
    )
    result = _run(_live, "canary", "10")
    assert result.returncode != 0 and "back on blue" in result.stderr
    assert _state(_live)["CANARY"] == "0"
    assert "20%" not in _routing(_live) and "10%" not in _routing(_live)


def test_promote_then_rollback(_live):
    assert _run(_live, "promote").returncode == 0
    assert _state(_live) == {"ACTIVE": "green", "CANARY": "0", "PREVIOUS": "blue"}
    assert "* app-green:8000;" in _routing(_live)
    assert _run(_live, "rollback").returncode == 0
    assert _state(_live) == {"ACTIVE": "blue", "CANARY": "0", "PREVIOUS": "green"}


def test_rollback_during_a_canary_only_stops_the_canary(_live):
    _run(_live, "canary", "30")
    assert _run(_live, "rollback").returncode == 0
    assert _state(_live) == {"ACTIVE": "blue", "CANARY": "0", "PREVIOUS": ""}


def test_rollback_needs_something_to_go_back_to(_live):
    assert _run(_live, "rollback").returncode != 0


def test_deploy_is_refused_during_a_canary(_live):
    _run(_live, "canary", "10")
    assert _run(_live, "deploy", "registry/app:3").returncode != 0


def test_failed_deploy_stops_the_new_colour_and_keeps_traffic(_sandbox):
    _run(_sandbox, "init")
    _set_health(_sandbox, "green", "starting")
    result = _run(_sandbox, "deploy", "registry/app:2")
    assert result.returncode != 0 and "untouched" in result.stderr
    assert not (_sandbox / "fake" / "running-green").exists()
    assert _state(_sandbox)["ACTIVE"] == "blue"


def test_nginx_rejecting_the_routing_keeps_the_old_file(_live):
    before = _routing(_live)
    (_live / "fake" / "reject").touch()
    assert _run(_live, "canary", "10").returncode != 0
    assert _routing(_live) == before
    assert _state(_live)["CANARY"] == "0"


def test_stop_idle_clears_previous(_live):
    _run(_live, "promote")
    assert _run(_live, "stop-idle").returncode == 0
    assert not (_live / "fake" / "running-blue").exists()
    assert _state(_live)["PREVIOUS"] == ""


def test_deploy_passes_the_image_to_the_idle_colour(_live):
    log = (_live / "fake" / "log").read_text()
    assert "up -d --force-recreate --no-deps app-green" in log


def test_status_reports_both_colours(_live):
    out = _run(_live, "status").stdout
    assert "live:     blue" in out and "idle:     green" in out


def _fake(sandbox, name, value=""):
    (sandbox / "fake" / name).write_text(value)


def _promote_with(_live, monkeypatch, **env):
    monkeypatch.setenv("SMOKE_RETRY_WAIT", "0")
    monkeypatch.setenv("PROMOTE_WATCH", "0")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert _run(_live, "deploy", "app:2").returncode == 0
    return _run(_live, "promote")


def test_smoke_uses_the_health_route_and_refuses_a_bad_answer(_live, monkeypatch):
    _fake(_live, "code-health", "500")
    result = _promote_with(_live, monkeypatch)
    assert result.returncode != 0
    assert "GET /api/v1/health on green answered 500" in result.stderr
    assert _state(_live)["ACTIVE"] == "blue"


def test_smoke_refuses_a_search_that_errors(_live, monkeypatch):
    _fake(_live, "code-search", "500")
    result = _promote_with(_live, monkeypatch)
    assert result.returncode != 0 and "term-overlap" in result.stderr
    assert "answered 500" in result.stderr and _state(_live)["ACTIVE"] == "blue"


def test_smoke_search_needs_no_results_only_a_200(_live, monkeypatch):
    result = _promote_with(_live, monkeypatch)
    assert result.returncode == 0 and _state(_live)["ACTIVE"] == "green"


def test_smoke_search_busy_is_only_a_warning(_live, monkeypatch):
    _fake(_live, "code-search", "503")
    result = _promote_with(_live, monkeypatch)
    assert result.returncode == 0 and "answered 503 (busy) twice" in result.stderr
    assert _state(_live)["ACTIVE"] == "green"


def test_smoke_skips_search_when_the_app_has_none(_live, monkeypatch):
    _fake(_live, "no-search")
    _fake(_live, "code-search", "500")
    result = _promote_with(_live, monkeypatch)
    assert result.returncode == 0 and "search check skipped" in result.stdout


def test_smoke_search_can_be_switched_off_or_pointed_elsewhere(_live, monkeypatch):
    _fake(_live, "code-search", "500")
    assert _promote_with(_live, monkeypatch, SMOKE_SEARCH="0").returncode == 0


def test_smoke_search_path_override_is_used(_live, monkeypatch):
    result = _promote_with(
        _live, monkeypatch, SMOKE_SEARCH_PATH="/api/v1/searches?q=x&method=tfidf"
    )
    assert result.returncode == 0
    assert "method=tfidf" in (_live / "fake" / "log").read_text()


def _images(sandbox, *refs):
    """Fake local images of the hadith-search repo, oldest first."""
    lines = [f"{ref} 2026-10-0{n + 1}T10:00:00.123456789Z" for n, ref in enumerate(refs)]
    (sandbox / "fake" / "images").write_text("\n".join(lines) + "\n")


def _left(sandbox):
    return [line.split()[0] for line in (sandbox / "fake" / "images").read_text().splitlines()]


def test_prune_keeps_the_newest_three_release_images(_live):
    _images(_live, *(f"hadith-search:r{n}" for n in range(1, 7)), "hadith-search:blue", "other:r0")
    result = _run(_live, "prune-images")
    assert result.returncode == 0, result.stderr
    assert _left(_live) == [
        "hadith-search:r4",
        "hadith-search:r5",
        "hadith-search:r6",
        "hadith-search:blue",
        "other:r0",
    ]


def test_prune_dry_run_changes_nothing(_live):
    _images(_live, *(f"hadith-search:r{n}" for n in range(1, 6)))
    result = _run(_live, "prune-images", "--dry-run")
    assert result.returncode == 0 and "dry run: would remove hadith-search:r1" in result.stdout
    assert len(_left(_live)) == 5
    assert "rmi" not in (_live / "fake" / "log").read_text()


def test_prune_protects_running_and_recorded_images(_live):
    _images(_live, *(f"hadith-search:r{n}" for n in range(1, 7)))
    _fake(_live, "running-images", "hadith-search:r1\nnginx:stable\n")
    (_live / "deploy" / "state" / "releases.env").write_text("GREEN_REL_IMAGE=hadith-search:r2\n")
    assert _run(_live, "prune-images", "--keep", "2").returncode == 0
    assert _left(_live) == [f"hadith-search:r{n}" for n in (1, 2, 5, 6)]


def test_prune_does_nothing_with_too_few_images(_live):
    _images(_live, "hadith-search:r1", "hadith-search:r2", "hadith-search:r3")
    result = _run(_live, "prune-images")
    assert result.returncode == 0 and "nothing to remove" in result.stdout
    assert len(_left(_live)) == 3


@pytest.mark.parametrize("value", ["0", "-1", "x", ""])
def test_prune_keep_must_be_a_positive_number(_live, value):
    _images(_live, *(f"hadith-search:r{n}" for n in range(1, 6)))
    assert _run(_live, "prune-images", "--keep", value).returncode != 0
    assert len(_left(_live)) == 5


def test_prune_never_uses_force_or_image_prune(_live):
    _images(_live, *(f"hadith-search:r{n}" for n in range(1, 6)))
    _run(_live, "prune-images")
    log = (_live / "fake" / "log").read_text()
    assert "rmi" in log and "rmi -f" not in log and "image prune" not in log


def test_prune_after_promote_is_off_unless_asked(_live, monkeypatch):
    _images(_live, *(f"hadith-search:r{n}" for n in range(1, 6)))
    _promote_with(_live, monkeypatch)
    assert len(_left(_live)) == 5
    assert _run(_live, "rollback").returncode == 0
    monkeypatch.setenv("PRUNE_AFTER_PROMOTE", "1")
    assert _run(_live, "promote", "--force").returncode == 0
    assert len(_left(_live)) == 3


def test_deploy_reads_the_staged_model_settings(_live):
    out = _run(_live, "deploy", "registry/app:3").stdout
    assert "model settings" not in out
    state = _live / "deploy" / "state"
    (state / "model.env").write_text("ARABIC_MODEL_DIR=/m/mv2\nEMBEDDINGS_RELEASE=mv2\n")
    out = _run(_live, "deploy", "registry/app:4").stdout
    assert "ARABIC_MODEL_DIR=/m/mv2 EMBEDDINGS_RELEASE=mv2" in out
    dry = _run(_live, "--dry-run", "deploy", "registry/app:5").stdout
    assert "model settings" not in dry
