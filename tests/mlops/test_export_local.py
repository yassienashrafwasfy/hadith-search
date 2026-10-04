"""Export a model folder that is already on disk: no download, no network."""

import json
import os

import numpy as np
import pytest

pytest.importorskip("sentence_transformers")
pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")

from scripts import arabic_encoder, export_onnx  # noqa: E402


@pytest.fixture
def _tiny_model(tmp_path):
    """A random 2-layer BERT with a 16-word vocabulary, saved as a sentence-transformers folder."""
    from sentence_transformers import SentenceTransformer, models
    from transformers import BertConfig, BertModel, BertTokenizerFast

    words = ["[PAD]", "[UNK]", "[CLS]", "[SEP]", "[MASK]"] + list("abcdefghijk")
    vocab = tmp_path / "vocab.txt"
    vocab.write_text("\n".join(words))
    hf = tmp_path / "hf"
    BertTokenizerFast(str(vocab)).save_pretrained(hf)
    config = BertConfig(
        vocab_size=len(words),
        hidden_size=64,
        num_hidden_layers=2,
        num_attention_heads=2,
        intermediate_size=64,
        max_position_embeddings=64,
    )
    BertModel(config).save_pretrained(hf)
    transformer = models.Transformer(str(hf))
    folder = tmp_path / "registry-version"
    SentenceTransformer(modules=[transformer, models.Pooling(64, "mean")]).save(str(folder))
    return folder


def test_export_from_a_local_folder_passes_parity_and_is_servable(
    _tiny_model, tmp_path, monkeypatch
):
    monkeypatch.setattr(export_onnx, "PARITY_TEXTS", ["a b c", "d e f g", "h"])
    info = export_onnx.export(
        str(tmp_path / "out"),
        source_dir=str(_tiny_model),
        source_name="enc/v2",
        source_revision="run-1",
    )
    assert info["model"] == "enc/v2" and info["revision"] == "run-1"
    assert info["min_parity_cosine"] >= export_onnx.PARITY_MIN_COSINE
    assert json.load(open(tmp_path / "out" / "export.json"))["model"] == "enc/v2"
    vectors = arabic_encoder.load_encoder(str(tmp_path / "out"), threads=1).encode(["a b", "c"])
    assert vectors.shape == (2, arabic_encoder.EMBEDDING_DIM)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-4)
    assert os.path.isfile(tmp_path / "out" / "model.onnx")
