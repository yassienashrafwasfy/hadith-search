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
