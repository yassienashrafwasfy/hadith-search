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
THREADS_ENV = "ARABIC_ENCODER_THREADS"
DEFAULT_THREADS = 1  # a query is one short text; see handoff item 24 for why not more
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


def serving_threads() -> int:
    """Threads for one encoder when serving. ONNX Runtime sizes its pool from the host's cores,
    not the container's CPU limit, and its idle threads spin, which used up a 2-CPU quota."""
    return int(os.environ.get(THREADS_ENV, DEFAULT_THREADS))


class OnnxEncoder:
    """`encode(texts)` returns an (n, EMBEDDING_DIM) float32 array of unit vectors.

    `threads` is the ONNX Runtime thread count; 0 lets it use every core (for building embeddings).
    """

    def __init__(self, model_path: str, tokenizer_path: str, threads: int = DEFAULT_THREADS):
        import onnxruntime
        from tokenizers import Tokenizer

        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        self._tokenizer.enable_truncation(max_length=MAX_LENGTH)
        self._tokenizer.no_padding()  # batches are padded here, per length-sorted batch
        self._pad_id = self._tokenizer.token_to_id("[PAD]")
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        if threads:
            options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self._session = onnxruntime.InferenceSession(
            model_path, options, providers=["CPUExecutionProvider"]
        )

    def encode(self, texts: list[str], batch_size: int = BATCH_SIZE, **_ignored) -> np.ndarray:
        if not texts:
            return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        encodings = self._tokenizer.encode_batch(texts)
        # Texts of similar length share a batch, so short ones are not padded to the longest.
        order = sorted(range(len(texts)), key=lambda i: len(encodings[i].ids))
        vectors = np.empty((len(texts), EMBEDDING_DIM), dtype=np.float32)
        for start in range(0, len(order), batch_size):
            chosen = order[start : start + batch_size]
            width = max(len(encodings[i].ids) for i in chosen)
            ids = np.full((len(chosen), width), self._pad_id, dtype=np.int64)
            mask = np.zeros((len(chosen), width), dtype=np.int64)
            for row, i in enumerate(chosen):
                n = len(encodings[i].ids)
                ids[row, :n] = encodings[i].ids
                mask[row, :n] = 1
            (out,) = self._session.run([OUTPUT_NAME], {"input_ids": ids, "attention_mask": mask})
            vectors[chosen] = out
        return vectors


def load_encoder(directory: str | None = None, threads: int | None = None) -> OnnxEncoder:
    directory = directory or model_dir()
    model_path = os.path.join(directory, "model.onnx")
    tokenizer_path = os.path.join(directory, "tokenizer.json")
    if not (os.path.isfile(model_path) and os.path.isfile(tokenizer_path)):
        raise FileNotFoundError(
            f"Arabic ONNX model not found in {directory}. "
            "Create it with: python -m scripts.export_onnx (from backend/)"
        )
    return OnnxEncoder(
        model_path, tokenizer_path, serving_threads() if threads is None else threads
    )
