"""Export the Arabic sentence encoder to ONNX and check it against the PyTorch original.

    python -m scripts.export_onnx [--out DIR] [--opset N]

Downloads `masterofaudio2077/Fada_ar_embedding` (an ARBERTv2-based Arabic BERT, mean pooling, trained
with Matryoshka loss and distilled from Qwen3-Embedding-8B), wraps it so the graph itself does the mean pooling, cuts the vector to
`EMBEDDING_DIM` and L2-normalises it, and writes `model.onnx`, `tokenizer.json` and
`export.json` into the output directory.

The opset is the newest one that both the exporter and ONNX Runtime accept AND whose output
matches the PyTorch model (see `check_parity`). Without `--opset` the script tries opsets from
the newest ONNX knows down to `MIN_OPSET` and keeps the first that passes; with `--opset` it
tries only that one.
"""

import argparse
import json
import os
import re
import sys

import numpy as np

from scripts.arabic_encoder import (
    EMBEDDING_DIM,
    MAX_LENGTH,
    MODEL_DIR_ENV,
    OUTPUT_NAME,
    encoding_text,
)

MODEL_ID = "masterofaudio2077/Fada_ar_embedding"
MIN_OPSET = 17
PARITY_MIN_COSINE = 0.9999
DEFAULT_OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "onnx", "arabic"
)

# Short, medium and long Arabic texts, so padding in a batch is exercised.
PARITY_TEXTS = [
    "ما حكم الصلاة في المسجد",
    "قال رسول الله صلى الله عليه وسلم: إنما الأعمال بالنيات وإنما لكل امرئ ما نوى",
    "الصيام جنة فإذا كان يوم صوم أحدكم فلا يرفث ولا يصخب",
    " ".join(["عن أبي هريرة رضي الله عنه قال قال رسول الله صلى الله عليه وسلم"] * 40),
    "الطهور شطر الإيمان",
]


def _wrapper(model):
    import torch

    class PooledEncoder(torch.nn.Module):
        """Mean pooling over real tokens, cut to EMBEDDING_DIM, then unit length."""

        def __init__(self, transformer):
            super().__init__()
            self.transformer = transformer

        def forward(self, input_ids, attention_mask):
            hidden = self.transformer(
                input_ids=input_ids, attention_mask=attention_mask
            ).last_hidden_state
            mask = attention_mask.unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
            return torch.nn.functional.normalize(pooled[:, :EMBEDDING_DIM], p=2, dim=1)

    return PooledEncoder(model).eval()


def _export(wrapper, tokenizer, opset: int, path: str) -> None:
    import torch

    sample = tokenizer(
        PARITY_TEXTS[:2], padding=True, truncation=True, max_length=64, return_tensors="pt"
    )
    batch, seq = torch.export.Dim("batch"), torch.export.Dim("seq", max=MAX_LENGTH)
    torch.onnx.export(
        wrapper,
        (sample["input_ids"], sample["attention_mask"]),
        path,
        input_names=["input_ids", "attention_mask"],
        output_names=[OUTPUT_NAME],
        dynamic_shapes={
            "input_ids": {0: batch, 1: seq},
            "attention_mask": {0: batch, 1: seq},
        },
        opset_version=opset,
        dynamo=True,
    )


def reference_vectors(model_dir_or_id: str, texts: list[str]) -> np.ndarray:
    """What the original PyTorch SentenceTransformer gives, cut and renormalised the same way."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_dir_or_id, device="cpu")
    model.max_seq_length = MAX_LENGTH  # the saved default is 200, but serving truncates at 512
    full = model.encode([encoding_text(t) for t in texts], convert_to_numpy=True)
    cut = full[:, :EMBEDDING_DIM]
    return cut / np.linalg.norm(cut, axis=1, keepdims=True)


def check_parity(onnx_path: str, tokenizer_path: str, reference: np.ndarray) -> float:
    """Smallest cosine between the ONNX vector and the PyTorch one over PARITY_TEXTS."""
    from scripts.arabic_encoder import OnnxEncoder

    encoder = OnnxEncoder(onnx_path, tokenizer_path, threads=0)
    # Encoded one at a time and as one padded batch: both must agree with PyTorch.
    singles = np.vstack([encoder.encode([t]) for t in PARITY_TEXTS])
    batched = encoder.encode(PARITY_TEXTS)
    return float(
        min((singles * reference).sum(axis=1).min(), (batched * reference).sum(axis=1).min())
    )


def written_opset(path: str) -> int:
    """The default-domain opset the file declares (the exporter may differ from the request)."""
    import onnx

    model = onnx.load(path, load_external_data=False)
    return next(o.version for o in model.opset_import if o.domain in ("", "ai.onnx"))


def _candidate_opsets(requested: int | None) -> list[int]:
    if requested is not None:
        return [requested]
    import onnx

    return list(range(onnx.defs.onnx_opset_version(), MIN_OPSET - 1, -1))


def export(out_dir: str, requested_opset: int | None = None) -> dict:
    import onnxruntime
    import torch
    import transformers
    from huggingface_hub import model_info, snapshot_download
    from transformers import AutoModel, AutoTokenizer

    os.makedirs(out_dir, exist_ok=True)
    revision = model_info(MODEL_ID).sha
    local = snapshot_download(MODEL_ID, revision=revision)
    tokenizer = AutoTokenizer.from_pretrained(local)
    wrapper = _wrapper(AutoModel.from_pretrained(local))
    reference = reference_vectors(local, PARITY_TEXTS)

    onnx_path = os.path.join(out_dir, "model.onnx")
    tokenizer_path = os.path.join(out_dir, "tokenizer.json")
    tried: dict[int, str] = {}
    for opset in _candidate_opsets(requested_opset):
        try:
            _export(wrapper, tokenizer, opset, onnx_path)
            tokenizer.backend_tokenizer.save(tokenizer_path)
            cosine = check_parity(onnx_path, tokenizer_path, reference)
        except Exception as exc:  # exporter or ORT rejected this opset
            tried[opset] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
            print(f"opset {opset}: failed ({tried[opset]})")
            continue
        if cosine < PARITY_MIN_COSINE:
            tried[opset] = f"parity {cosine:.6f} below {PARITY_MIN_COSINE}"
            print(f"opset {opset}: {tried[opset]}")
            continue
        info = {
            "model": MODEL_ID,
            "revision": revision,
            "opset": written_opset(onnx_path),
            "requested_opset": opset,
            "embedding_dim": EMBEDDING_DIM,
            "max_length": MAX_LENGTH,
            "min_parity_cosine": cosine,
            "rejected_opsets": tried,
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "onnxruntime": onnxruntime.__version__,
        }
        with open(os.path.join(out_dir, "export.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, indent=2)
        print(
            f"opset {opset} (file declares {info['opset']}): ok, min cosine vs PyTorch {cosine:.6f}"
        )
        return info
    raise RuntimeError(f"No opset passed: {tried}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=os.environ.get(MODEL_DIR_ENV, DEFAULT_OUT))
    parser.add_argument("--opset", type=int, default=None)
    args = parser.parse_args(argv)
    info = export(args.out, args.opset)
    print(re.sub(r"\n\s*", " ", json.dumps(info)))


if __name__ == "__main__":
    sys.exit(main())
