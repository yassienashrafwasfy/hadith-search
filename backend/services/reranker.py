"""Cross-encoder reranker shared by the hadith search and the Quran agent.

The default model is ARA-Reranker-V1 (XLM-RoBERTa, 0.6B, Arabic). It reads a (question, passage)
pair jointly, which a bi-encoder like Fada cannot. The model is loaded once per process, on first
use. `rerank_texts` is the blocking call the sync search path uses; `CrossEncoderReranker` wraps
it in a worker thread for the async Quran agent.
"""

import asyncio
import re
from collections.abc import Sequence
from functools import lru_cache

QURAN_MAX_CHARS = 400  # verses and short passages
HADITH_MAX_CHARS = 1200  # a hadith matn is longer; the model window is 512 tokens
_MARKS = re.compile("[ـً-ٰٟۖ-ۭ]")


def plain(text: str) -> str:
    """Diacritics and tatweel removed, alef wasla normalised: how the reranker sees Arabic."""
    return " ".join(_MARKS.sub("", text).replace("ٱ", "ا").split())


@lru_cache(maxsize=1)
def load_cross_encoder(name: str):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(name)


def score_pairs(
    model, question: str, texts: Sequence[str], max_chars: int = QURAN_MAX_CHARS
) -> list[float]:
    """Relevance in [0, 1] of each text to the question (blocking).

    A single-label CrossEncoder already applies a sigmoid in `predict`; do not apply another.
    """
    pairs = [(plain(question), plain(t)[:max_chars]) for t in texts]
    return [float(s) for s in model.predict(pairs)]


def rerank_texts(
    name: str, question: str, texts: Sequence[str], max_chars: int = QURAN_MAX_CHARS
) -> list[float]:
    """Load the model on first use and score every text against the question (blocking)."""
    if not texts:
        return []
    return score_pairs(load_cross_encoder(name), question, texts, max_chars)


class CrossEncoderReranker:
    """`await reranker(question, texts)` -> one score per text, in the order given."""

    def __init__(self, name: str, max_chars: int = QURAN_MAX_CHARS):
        self.name, self.max_chars = name, max_chars

    async def __call__(self, question: str, texts: Sequence[str]) -> list[float]:
        return await asyncio.to_thread(rerank_texts, self.name, question, texts, self.max_chars)
