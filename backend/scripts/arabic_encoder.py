"""Arabic sentence encoder served through ONNX Runtime (no torch at query time).

The model is `akhooli/sbert-nli-500k-triplets-MB`, exported by `scripts/export_onnx.py`. The
ONNX graph does the mean pooling, keeps the first `EMBEDDING_DIM` values (the model was trained
with Matryoshka loss, so a cut vector is meant to work) and normalises to unit length. It has no
query or passage prefix: queries and hadiths go through the same `encoding_text`.
"""

import os
import re

import numpy as np

EMBEDDING_DIM = 256
MAX_LENGTH = 512  # the model accepts 8192, but it was trained on texts of up to ~250 tokens
MODEL_DIR_ENV = "ARABIC_MODEL_DIR"
DEFAULT_MODEL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "onnx", "arabic"
)
BATCH_SIZE = 32
OUTPUT_NAME = "sentence_embedding"


def encoding_text(text: str) -> str:
    """Light cleanup, the same for queries and hadiths: drop diacritics, collapse whitespace.

    The model was trained on ordinary Arabic text, so this does not use the heavier
    normalisation of the BM25 index (letter variants, stripping non-Arabic characters).
    """
    from camel_tools.utils.dediac import dediac_ar

    return re.sub(r"\s+", " ", dediac_ar(text)).strip()


def model_dir() -> str:
    return os.environ.get(MODEL_DIR_ENV, DEFAULT_MODEL_DIR)


class OnnxEncoder:
    """`encode(texts)` returns an (n, EMBEDDING_DIM) float32 array of unit vectors."""

    def __init__(self, model_path: str, tokenizer_path: str):
        import onnxruntime
        from tokenizers import Tokenizer

        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        self._tokenizer.enable_truncation(max_length=MAX_LENGTH)
        self._session = onnxruntime.InferenceSession(model_path, providers=["CPUExecutionProvider"])

    def encode(self, texts: list[str], batch_size: int = BATCH_SIZE, **_ignored) -> np.ndarray:
        if not texts:
            return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        batches = []
        for start in range(0, len(texts), batch_size):
            encodings = self._tokenizer.encode_batch(texts[start : start + batch_size])
            (vectors,) = self._session.run(
                [OUTPUT_NAME],
                {
                    "input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
                    "attention_mask": np.array(
                        [e.attention_mask for e in encodings], dtype=np.int64
                    ),
                },
            )
            batches.append(vectors)
        return np.vstack(batches).astype(np.float32, copy=False)


def load_encoder(directory: str | None = None) -> OnnxEncoder:
    directory = directory or model_dir()
    model_path = os.path.join(directory, "model.onnx")
    tokenizer_path = os.path.join(directory, "tokenizer.json")
    if not (os.path.isfile(model_path) and os.path.isfile(tokenizer_path)):
        raise FileNotFoundError(
            f"Arabic ONNX model not found in {directory}. "
            "Create it with: python -m scripts.export_onnx (from backend/)"
        )
    return OnnxEncoder(model_path, tokenizer_path)
