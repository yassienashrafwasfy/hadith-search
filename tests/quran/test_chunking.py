import numpy as np
import pytest

from quran.chunking import adjacent_similarities, semantic_passages


def _case(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(0, 80))
    tokens = [int(t) for t in rng.integers(1, 120, size=n)]
    vectors = rng.normal(size=(n, 8))
    lo = int(rng.integers(1, 60))
    hi = int(rng.integers(lo, 250))
    pct = float(rng.uniform(0, 100))
    return tokens, vectors, pct, lo, hi


@pytest.mark.parametrize("seed", range(200))
def test_partition_and_bounds(seed):
    tokens, vectors, pct, lo, hi = _case(seed)
    out = semantic_passages(tokens, vectors, percentile=pct, min_tokens=lo, max_tokens=hi)
    flat = [i for r in out for i in r]
    assert flat == list(range(len(tokens)))
    assert all(len(r) > 0 and r.step == 1 for r in out)
    for r in out:
        assert sum(tokens[i] for i in r) <= hi or len(r) == 1
    for r in out[:-1]:
        assert sum(tokens[i] for i in r) >= lo or sum(tokens[i] for i in r) + tokens[r.stop] > hi
    assert out == semantic_passages(tokens, vectors, percentile=pct, min_tokens=lo, max_tokens=hi)


def test_splits_at_topic_change():
    a, b = np.array([1.0, 0.0]), np.array([0.0, 1.0])
    vectors = np.array([a, a, a, b, b, b])
    out = semantic_passages([30] * 6, vectors, percentile=20, min_tokens=60, max_tokens=200)
    assert out == [range(0, 3), range(3, 6)]


def test_max_forces_split_and_long_ayah_stays_whole():
    vectors = np.ones((4, 3))
    out = semantic_passages([50, 50, 500, 50], vectors, min_tokens=10, max_tokens=120)
    assert out == [range(0, 2), range(2, 3), range(3, 4)]


def test_short_tail_merged():
    a, b = np.array([1.0, 0.0]), np.array([0.0, 1.0])
    out = semantic_passages([50, 50, 5], np.array([a, a, b]), percentile=50, min_tokens=40)
    assert out == [range(0, 3)]


def test_edge_cases():
    assert semantic_passages([], np.zeros((0, 4))) == []
    assert semantic_passages([9], np.ones((1, 4))) == [range(0, 1)]
    assert list(adjacent_similarities(np.zeros((3, 2)))) == [0.0, 0.0]
    with pytest.raises(ValueError):
        semantic_passages([1, 2], np.ones((3, 2)))
    with pytest.raises(ValueError):
        semantic_passages([1], np.ones((1, 2)), min_tokens=9, max_tokens=3)
