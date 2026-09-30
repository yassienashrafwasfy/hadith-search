import math

import pytest

from scripts import evaluation as ev


def test_precision_recall_f1():
    retrieved, relevant = {1, 2, 3}, {1, 4}
    assert ev.precision(retrieved, relevant) == pytest.approx(1 / 3)
    assert ev.recall(retrieved, relevant) == pytest.approx(1 / 2)
    assert ev.f1_score(retrieved, relevant) == pytest.approx(0.4)


def test_at_k_truncates():
    assert ev.precision_at_k([9, 1, 2], {1, 2}, 1) == 0.0
    assert ev.precision_at_k([9, 1, 2], {1, 2}, 3) == pytest.approx(2 / 3)


def test_average_precision_and_map():
    assert ev.average_precision([1, 9, 2], {1, 2}) == pytest.approx((1 + 2 / 3) / 2)
    assert ev.MAP([[1, 9, 2]], [{1, 2}]) == pytest.approx((1 + 2 / 3) / 2)


def test_jaccard():
    assert ev.jaccard_similarity({1, 2}, {2, 3}) == pytest.approx(1 / 3)


def test_reciprocal_rank():
    assert ev.reciprocal_rank([9, 8, 1], {1}) == pytest.approx(1 / 3)
    assert ev.reciprocal_rank([9], {1}) == 0.0
    assert ev.mean_reciprocal_rank([[1], [9, 1]], [{1}, {1}]) == pytest.approx(0.75)
    assert ev.mean_reciprocal_rank([], []) == 0


def test_dcg_and_ndcg(_graded_relevant):
    expected = (2**2 - 1) / math.log2(2) + (2**1 - 1) / math.log2(3)
    assert ev.dcg([1, 2, 3], _graded_relevant) == pytest.approx(expected)
    assert ev.normalized_dcg([1, 2, 3], _graded_relevant) == pytest.approx(1.0)
    assert ev.normalized_dcg([2, 1], _graded_relevant) < 1.0


def test_ndcg_no_relevant_is_zero():
    assert ev.normalized_dcg([1], {1: 0}) == 0


def test_evaluate_query_columns(_graded_relevant):
    df = ev.evaluate_query([1, 2], _graded_relevant)
    assert list(df.columns) == ["Precision", "Recall", "F1_Score", "AP", "IoU", "NDCG"]
    assert df.loc[0, "Precision"] == 1.0
