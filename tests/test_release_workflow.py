"""The release workflow publishes to the private registry only for version tags, never for PRs."""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"


def _load():
    return yaml.safe_load(WORKFLOW.read_text())


def test_it_runs_only_for_version_tags():
    # YAML reads the key `on` as the boolean True
    triggers = _load()[True]
    assert set(triggers) == {"push"}
    assert set(triggers["push"]) == {"tags"}
    assert all(tag.startswith("v[0-9]") for tag in triggers["push"]["tags"])


def test_packages_write_is_only_on_the_release_job():
    workflow = _load()
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["release"]["permissions"]["packages"] == "write"


def test_it_checks_main_and_ci_before_pushing_and_never_overwrites_a_tag():
    text = WORKFLOW.read_text()
    assert text.index("is on main and CI passed") < text.index("push: true")
    assert text.index("Tags are never overwritten") < text.index("push: true")


def test_every_action_is_pinned_to_a_commit():
    for step in _load()["jobs"]["release"]["steps"]:
        if "uses" in step:
            assert len(step["uses"].split("@")[1]) == 40, step["uses"]
