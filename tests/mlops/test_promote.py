import json
import os

import numpy as np
import pytest
from sqlalchemy import func, inspect, select
from tests.mlops.conftest import PAIRS, run_args

from scripts import export_onnx
from scripts import promote_model as pm

pytestmark = pytest.mark.usefixtures("_fake_encoders")


@pytest.fixture
def _stage(tmp_path):
    return tmp_path / "stage"


def _tree(path) -> list[str]:
    return sorted(str(p) for p in path.rglob("*")) if path.exists() else []


def test_dry_run_prints_the_pick_and_changes_nothing(
    _registry, _live_dir, _state_dir, _stage, capsys, monkeypatch
):
    monkeypatch.setattr(export_onnx, "export", lambda *a, **k: pytest.fail("must not export"))
    before = _tree(_state_dir)
    assert pm.main(run_args(_live_dir, _stage, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "version 1: 0.9502" in out and "version 2: 1.0000" in out
    assert "best: version 2" in out and "beats the live model" in out
    assert "dry run: would stage release mv2" in out and "nothing was written" in out
    assert not _stage.exists() and _tree(_state_dir) == before


def test_margin_gate_fails_when_the_best_is_not_enough_better(
    _registry, _live_dir, _state_dir, _stage, capsys
):
    # best 1.0 vs live 0.95024: the gap is 0.0498
    assert pm.main(run_args(_live_dir, _stage, "--margin", "0.06")) == pm.EXIT_NO_WINNER
    assert "does not beat it" in capsys.readouterr().out
    assert not _stage.exists() and not os.path.exists(_state_dir / "model.staged.env")
    assert pm.main(run_args(_live_dir, _stage, "--margin", "0.04", "--dry-run")) == 0


def test_the_live_version_is_not_a_candidate(_registry, _live_dir, _state_dir, _stage, capsys):
    (_state_dir / "model.env").write_text("MODEL_VERSION=2\n")
    assert pm.main(run_args(_live_dir, _stage, "--dry-run")) == pm.EXIT_NO_WINNER
    assert "best: version 1" in capsys.readouterr().out  # only version 1 was left, no gain


def test_missing_pairs_artifact_is_a_clear_error(_registry, _live_dir, _state_dir, _stage, capsys):
    assert pm.main(run_args(_live_dir, _stage, "--pairs-artifact", "eval/nope.json")) == 2
    err = capsys.readouterr().err
    assert "no artifact 'eval/nope.json'" in err and "eval pairs" in err


def test_versions_with_different_pairs_are_refused(
    _registry, _live_dir, _state_dir, _stage, tmp_path, capsys
):
    other = tmp_path / "other" / "pairs.json"
    other.parent.mkdir()
    other.write_text(json.dumps(PAIRS[:2] + [{"query": "q1", "text": "c", "label": 2}]))
    _registry("acb", other)
    assert pm.main(run_args(_live_dir, _stage)) == 2
    assert "different eval pairs" in capsys.readouterr().err
    # reading one pairs file for all of them is allowed


def test_settings_errors(_live_dir, _stage, monkeypatch, capsys):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    assert pm.main(run_args(_live_dir, _stage)) == 2
    assert "MLFLOW_TRACKING_URI" in capsys.readouterr().err
    assert pm.main(["run", "--stage-dir", str(_stage)]) == 2
    assert "--registered-model" in capsys.readouterr().err
    (_live_dir / "model.onnx").unlink()
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "file:///nowhere")
    assert pm.main(run_args(_live_dir, _stage)) == 2
    assert "no live ONNX model" in capsys.readouterr().err


# --- the real steps against a scratch database -------------------------------------------


class _Calls:
    def __init__(self):
        self.exports, self.encodes, self.fail_export, self.fail_embed = [], 0, False, False


@pytest.fixture
def _calls(monkeypatch):
    calls = _Calls()

    def fake_export(out_dir, **kwargs):
        calls.exports.append(kwargs["source_name"])
        if calls.fail_export:
            raise RuntimeError("no opset passed the parity check")
        os.makedirs(out_dir, exist_ok=True)
        for name in ("model.onnx", "tokenizer.json"):
            open(os.path.join(out_dir, name), "w").write("x")
        return {"min_parity_cosine": 0.99999}

    class Encoder:
        def encode(self, texts):
            if calls.fail_embed:
                raise OSError("disk full")
            calls.encodes += len(texts)
            return np.tile(np.eye(64)[0], (len(texts), 1)) + 0.01 * np.arange(len(texts))[:, None]

    monkeypatch.setattr(export_onnx, "export", fake_export)
    monkeypatch.setattr(pm, "load_encoder", lambda directory, threads=None: Encoder())
    return calls


def _release_rows(table_name: str) -> int | None:
    from database import get_sync_engine

    engine = get_sync_engine()
    if table_name not in inspect(engine).get_table_names():
        return None
    from models.embedding_sets import embedding_table

    with engine.connect() as conn:
        return conn.execute(
            select(func.count()).select_from(embedding_table(table_name.split("_", 2)[2]))
        ).scalar_one()


def test_full_run_stages_a_new_release_and_leaves_the_default_table_alone(
    _registry, _live_dir, _state_dir, _stage, _patched_paths, _calls, capsys
):
    from database import get_sync_session
    from models import EmbeddingSet, HadithEmbedding

    with get_sync_session() as session:
        before = session.scalar(select(func.count()).select_from(HadithEmbedding))
    assert pm.main(run_args(_live_dir, _stage)) == 0
    assert _calls.exports == ["enc/v2"] and _calls.encodes == 3
    assert (_stage / "mv2" / "model.onnx").exists()
    env = pm._read_env(str(_state_dir / "model.staged.env"))
    assert env == {
        "ARABIC_MODEL_DIR": "/app/backend/data/onnx/releases/mv2",
        "EMBEDDINGS_RELEASE": "mv2",
        "MODEL_VERSION": "2",
    }
    assert not (_state_dir / "model.env").exists()  # only promote makes it live
    assert _release_rows("hadith_embeddings_mv2") == 3
    with get_sync_session() as session:
        assert session.scalar(select(func.count()).select_from(HadithEmbedding)) == before
        row = session.get(EmbeddingSet, "mv2")
        assert (row.model_version, row.dim) == ("2", 64)
    assert "to start it and move traffic" in capsys.readouterr().out


def test_parity_failure_aborts_before_anything_is_embedded(
    _registry, _live_dir, _state_dir, _stage, _patched_paths, _calls, capsys
):
    _calls.fail_export = True
    assert pm.main(run_args(_live_dir, _stage)) == pm.EXIT_PARITY
    assert "ONNX export failed" in capsys.readouterr().err
    assert _calls.encodes == 0 and _release_rows("hadith_embeddings_mv2") is None
    assert not (_state_dir / "model.staged.env").exists()


def test_a_failed_run_resumes_where_it_stopped(
    _registry, _live_dir, _state_dir, _stage, _patched_paths, _calls
):
    _calls.fail_embed = True
    with pytest.raises(OSError):
        pm.main(run_args(_live_dir, _stage))
    steps = pm._read_steps(str(_stage), "mv2")["steps"]
    assert list(steps) == ["exported"] and not (_state_dir / "model.staged.env").exists()
    _calls.fail_embed = False
    assert pm.main(run_args(_live_dir, _stage)) == 0
    assert _calls.exports == ["enc/v2"]  # not exported again
    assert list(pm._read_steps(str(_stage), "mv2")["steps"]) == ["exported", "embedded", "staged"]
    assert pm.main(run_args(_live_dir, _stage)) == 0  # all done: nothing is repeated
    assert _calls.exports == ["enc/v2"] and _calls.encodes == 3


def test_prune_keeps_the_newest_three_and_never_a_protected_release(
    _patched_paths, _state_dir, _stage, capsys
):
    from database import get_sync_engine, get_sync_session
    from models import EmbeddingSet
    from models.embedding_sets import embedding_table

    with get_sync_session() as session:
        for n in range(1, 6):
            with get_sync_engine().begin() as conn:
                embedding_table(f"mv{n}").create(conn)
            os.makedirs(_stage / f"mv{n}")
            session.add(
                EmbeddingSet(
                    release=f"mv{n}", model_version=str(n), dim=64, created_at=f"2026-01-0{n}"
                )
            )
        session.commit()
    (_state_dir / "model.env").write_text("EMBEDDINGS_RELEASE=mv1\n")  # the live one is old
    argv = ["prune", "--stage-dir", str(_stage)]
    assert pm.main(argv + ["--dry-run"]) == 0
    assert _release_rows("hadith_embeddings_mv2") == 0  # dry run removed nothing
    assert pm.main(argv) == 0
    assert _release_rows("hadith_embeddings_mv2") is None  # the only one outside newest 3 + live
    assert all(_release_rows(f"hadith_embeddings_mv{n}") == 0 for n in (1, 3, 4, 5))
    assert sorted(os.listdir(_stage)) == ["mv1", "mv3", "mv4", "mv5"]


def test_mlflow_settings_are_read_from_the_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MLFLOW_TRACKING_URI=https://example.test/x.mlflow\nOTHER=1\nMLFLOW_EMPTY=\n"
    )
    monkeypatch.setattr(pm, "ENV_FILE", env_file)
    for name in ("MLFLOW_TRACKING_URI", "OTHER", "MLFLOW_EMPTY"):
        monkeypatch.delenv(name, raising=False)
    pm._load_mlflow_env()
    assert os.environ["MLFLOW_TRACKING_URI"] == "https://example.test/x.mlflow"
    assert "OTHER" not in os.environ and "MLFLOW_EMPTY" not in os.environ
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://already.set/y")
    pm._load_mlflow_env()
    assert os.environ["MLFLOW_TRACKING_URI"] == "https://already.set/y"
