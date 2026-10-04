"""A local file-based MLflow store with a registered model of two versions, and fake encoders.

No server, no model download: a "model" is a folder holding `modules.json` (so it looks like a
sentence-transformers folder) and `vectors.json`, a table text -> vector that the fake encoders
read. Hand-computed nDCG@10 for the query "q1" (labels a=2, b=0, c=1):
  good   ranks a, c, b -> 1.0
  medium ranks a, b, c -> (2 + 0 + 1/log2(4)) / (2 + 1/log2(3)) = 2.5 / 2.63093 = 0.95024
  bad    ranks b, c, a -> (0 + 1/log2(3) + 2/log2(4)) / 2.63093 = 1.63093 / 2.63093 = 0.61989
"""

import json
import os

import numpy as np
import pytest

from scripts import promote_model

DIM = 64
PAIRS = [
    {"query": "q1", "text": "a", "label": 2},
    {"query": "q1", "text": "b", "label": 0},
    {"query": "q1", "text": "c", "label": 1},
]


def _one_hot(*pairs: tuple[int, float]) -> list[float]:
    v = np.zeros(DIM)
    for index, weight in pairs:
        v[index] = weight
    return v.tolist()


def _vectors(order: str) -> dict:
    """Vectors that make the cosine ranking of a, b, c for query q1 come out as `order`."""
    table = {"q1": _one_hot((0, 1.0))}
    for rank, text in enumerate(order):
        weight = (0.9, 0.6, 0.3)[rank]
        table[text] = _one_hot((0, weight), (1 + rank, 1.0))
    return table


def _write_model(folder, order: str):
    os.makedirs(folder, exist_ok=True)
    (folder / "modules.json").write_text("[]")
    (folder / "vectors.json").write_text(json.dumps(_vectors(order)))


def _encoder_from(model_dir: str):
    with open(os.path.join(model_dir, "vectors.json")) as f:
        table = json.load(f)
    return lambda texts: np.array([table[t] for t in texts])


@pytest.fixture
def _fake_encoders(monkeypatch):
    monkeypatch.setattr(promote_model, "_torch_encoder", _encoder_from)
    monkeypatch.setattr(promote_model, "_onnx_encoder", _encoder_from)


@pytest.fixture
def _state_dir(tmp_path, monkeypatch):
    path = tmp_path / "state"
    path.mkdir()
    monkeypatch.setattr(promote_model, "STATE_DIR", str(path))
    return path


@pytest.fixture
def _live_dir(tmp_path):
    """The live model folder; its quality is MEDIUM."""
    live = tmp_path / "live"
    _write_model(live, "abc")
    (live / "model.onnx").write_bytes(b"x")
    return live


@pytest.fixture
def _registry(tmp_path, monkeypatch):
    """A file store with registered model 'enc': version 1 is medium, version 2 is good."""
    pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    monkeypatch.setenv(
        "MLFLOW_ALLOW_FILE_STORE", "true"
    )  # mlflow 3 calls the file store deprecated
    uri = (tmp_path / "mlruns").as_uri()
    monkeypatch.setenv("MLFLOW_TRACKING_URI", uri)
    import mlflow

    mlflow.set_tracking_uri(uri)
    client = mlflow.MlflowClient()
    client.create_registered_model("enc")
    pairs = tmp_path / "pairs" / "pairs.json"
    pairs.parent.mkdir()
    pairs.write_text(json.dumps(PAIRS))

    def add_version(order: str, pairs_file=pairs):
        folder = tmp_path / f"model_{order}"
        _write_model(folder, order)
        with mlflow.start_run() as run:
            mlflow.log_artifacts(str(folder), "model")
            mlflow.log_artifact(str(pairs_file), "eval")
        return client.create_model_version(
            "enc", f"{run.info.artifact_uri}/model", run_id=run.info.run_id
        )

    add_version("abc")  # medium
    add_version("acb")  # good
    return add_version


def run_args(live_dir, stage_dir, *extra):
    return [
        "run",
        "--registered-model",
        "enc",
        "--live-model-dir",
        str(live_dir),
        "--stage-dir",
        str(stage_dir),
        *extra,
    ]


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch, tmp_path):
    """The tests must not read the developer's real `.env` (it may hold a tracking URI and token)."""
    monkeypatch.setattr(promote_model, "ENV_FILE", tmp_path / "no.env")
