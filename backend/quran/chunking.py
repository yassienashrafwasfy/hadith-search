"""Semantic chunking of one surah's ayat into passages. Pure numpy, no DB, deterministic."""

from collections.abc import Sequence

import numpy as np

DEFAULT_PERCENTILE = 25.0
DEFAULT_MIN_TOKENS = 40
DEFAULT_MAX_TOKENS = 200


def adjacent_similarities(vectors: np.ndarray) -> np.ndarray:
    """Cosine similarity of each ayah with the next one (length n-1; zero vectors give 0)."""
    v = np.asarray(vectors, dtype=np.float64)
    norms = np.linalg.norm(v, axis=1)
    safe = np.where(norms > 0, norms, 1.0)
    unit = v / safe[:, None]
    sims = np.einsum("ij,ij->i", unit[:-1], unit[1:])
    both = (norms[:-1] > 0) & (norms[1:] > 0)
    return np.where(both, sims, 0.0)


def _boundaries(sims: np.ndarray, tokens: Sequence[int], threshold: float, lo: int, hi: int):
    """Indices i such that a new passage starts at ayah i (greedy, left to right)."""
    starts: list[int] = []
    size = tokens[0]
    for i in range(1, len(tokens)):
        over = size + tokens[i] > hi
        dip = sims[i - 1] < threshold and size >= lo
        if over or dip:
            starts.append(i)
            size = 0
        size += tokens[i]
    return starts


def _merge_short_tail(starts: list[int], tokens: Sequence[int], lo: int, hi: int) -> list[int]:
    """Drop the last boundary when the final passage is under `lo` and the merge fits `hi`."""
    if not starts:
        return starts
    prev = starts[-2] if len(starts) > 1 else 0
    tail = sum(tokens[starts[-1] :])
    if tail < lo and tail + sum(tokens[prev : starts[-1]]) <= hi:
        return starts[:-1]
    return starts


def semantic_passages(
    ayat: Sequence[int],
    vectors: np.ndarray,
    *,
    percentile: float = DEFAULT_PERCENTILE,
    min_tokens: int = DEFAULT_MIN_TOKENS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[range]:
    """Split a surah into contiguous passages.

    `ayat[i]` is the token count of the i-th ayah in order; `vectors[i]` is its embedding.
    Returns ranges of 0-based positions into `ayat` that cover every ayah exactly once, in
    order. A new passage starts where the cosine between neighbours is below the `percentile`
    of the surah's adjacent similarities (only once the passage holds `min_tokens`), or where
    adding the next ayah would exceed `max_tokens`. An ayah is never split, so a single ayah
    longer than `max_tokens` is its own passage. A final passage under `min_tokens` is merged
    into the previous one when the result fits `max_tokens`.
    """
    n = len(ayat)
    if n != len(vectors):
        raise ValueError("ayat and vectors must have the same length")
    if min_tokens > max_tokens:
        raise ValueError("min_tokens must not exceed max_tokens")
    if n == 0:
        return []
    if n == 1:
        return [range(0, 1)]
    sims = adjacent_similarities(vectors)
    threshold = float(np.percentile(sims, percentile))
    starts = _boundaries(sims, ayat, threshold, min_tokens, max_tokens)
    starts = _merge_short_tail(starts, ayat, min_tokens, max_tokens)
    edges = [0, *starts, n]
    return [range(a, b) for a, b in zip(edges, edges[1:], strict=False)]
