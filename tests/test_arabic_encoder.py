import json
import os

import numpy as np
import pytest

from scripts import arabic_encoder as ae


def test_encoding_text_drops_diacritics_and_collapses_space():
    assert ae.encoding_text(" الصَّلَاة \n خير ") == "الصلاة خير"


def test_missing_model_says_how_to_export(tmp_path):
    with pytest.raises(FileNotFoundError, match="scripts.export_onnx"):
        ae.load_encoder(str(tmp_path))


def test_model_dir_can_be_overridden(monkeypatch):
    monkeypatch.setenv(ae.MODEL_DIR_ENV, "/somewhere")
    assert ae.model_dir() == "/somewhere"


def test_empty_input_gives_empty_matrix():
    encoder = ae.OnnxEncoder.__new__(ae.OnnxEncoder)
    assert encoder.encode([]).shape == (0, ae.EMBEDDING_DIM)


_REAL = ae.model_dir()


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(_REAL, "export.json")), reason="Arabic ONNX model not exported"
)
def test_real_model_vectors_are_unit_length_and_padding_independent():
    encoder = ae.load_encoder()
    short, long = "الطهور شطر الإيمان", "إنما الأعمال بالنيات وإنما لكل امرئ ما نوى"
    alone = encoder.encode([short])
    batched = encoder.encode([short, long])
    assert batched.shape == (2, ae.EMBEDDING_DIM)
    assert np.allclose(np.linalg.norm(batched, axis=1), 1.0, atol=1e-4)
    assert np.allclose(alone[0], batched[0], atol=1e-4)
    info = json.load(open(os.path.join(_REAL, "export.json")))
    assert info["min_parity_cosine"] >= 0.9999
