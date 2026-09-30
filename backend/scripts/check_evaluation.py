"""Run with: python -m scripts.check_evaluation"""
from scripts.evaluation import evaluate_system, evaluate_query, evaluate_query_at_k

grades = {1: 0, 2: 1, 3: 2}
row = evaluate_system(["EN01"], [[1, 2]], [grades]).loc["EN01"]
assert row["RR"] == 0.5
assert row["AP"] == 0.25
assert row["P@20"] == 0.05
assert row["R@20"] == 0.5
assert 0 < row["nDCG@20"] < 1
assert evaluate_query([1], grades).iloc[0]["Precision"] == 0
assert evaluate_query_at_k([1], grades, 20).iloc[0]["Recall@20"] == 0
print("Evaluation checks passed")
