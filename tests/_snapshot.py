"""Tiny snapshot helper: compare against tests/snapshots/<name>; set UPDATE_SNAPSHOTS=1 to rewrite."""

import json
import os
import pathlib

import pytest

_DIR = pathlib.Path(__file__).parent / "snapshots"


def _approx(value):
    if isinstance(value, dict):
        return {k: _approx(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_approx(v) for v in value]
    if isinstance(value, float):
        return pytest.approx(value, rel=1e-9, abs=1e-12)
    return value


def assert_matches(name: str, actual) -> None:
    path = _DIR / name
    is_json = name.endswith(".json")
    if os.environ.get("UPDATE_SNAPSHOTS"):
        path.write_text(
            json.dumps(actual, indent=1, ensure_ascii=False, sort_keys=True) if is_json else actual,
            encoding="utf-8",
        )
    expected = path.read_text(encoding="utf-8")
    if is_json:
        # round-trip so tuples/ints in `actual` compare like the stored JSON
        normalized = json.loads(json.dumps(actual, ensure_ascii=False))
        assert normalized == _approx(json.loads(expected))
    else:
        assert actual == expected
