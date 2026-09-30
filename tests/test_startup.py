import pytest

import startup
from features import Features


def test_run_step_prints_label(capsys):
    called = []
    startup._run_step("thing", lambda: called.append(1))
    assert called == [1] and "thing" in capsys.readouterr().out


def test_model_steps_need_dense_and_eager():
    assert startup._model_steps(Features(dense_retrieval=False, eager_model=True)) == []
    assert startup._model_steps(Features(dense_retrieval=True, eager_model=False)) == []
    ((label, load),) = startup._model_steps(Features(dense_retrieval=True, eager_model=True))
    assert "Arabic" in label and callable(load)


def test_index_step_warns_when_the_index_is_empty(_patched_paths, capsys):
    startup.check_index()
    assert "no postings" in capsys.readouterr().out


def test_index_step_is_quiet_when_the_index_is_built(_search_index, capsys):
    startup.check_index()
    assert capsys.readouterr().out == ""


def test_preload_skipped_when_search_disabled(monkeypatch, capsys):
    monkeypatch.setattr(startup, "_index_steps", lambda: pytest.fail("must not load"))
    startup.preload_resources(Features(search=False))
    assert "Search disabled" in capsys.readouterr().out


def test_preload_runs_every_step(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(startup, "_index_steps", lambda: [("a", lambda: ran.append("a"))])
    monkeypatch.setattr(startup, "_model_steps", lambda f: [("m", lambda: ran.append("m"))])
    startup.preload_resources(Features(dense_retrieval=True, eager_model=False))
    assert ran == ["a", "m"]
    assert "lazy-loading the Arabic encoder" in capsys.readouterr().out
    startup.preload_resources(Features(dense_retrieval=False))
    assert "lazy-loading" not in capsys.readouterr().out


async def test_init_database_creates_tables(_patched_paths):
    await startup.init_database()  # idempotent on an initialised DB
