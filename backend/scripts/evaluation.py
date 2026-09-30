from math import log2

import pandas as pd

graded_relevant_list = dict[int:int]


def precision(retrieved: set[int], relevant: set[int]) -> float:
    ret = len(retrieved)
    return len(retrieved & relevant) / ret if ret != 0 else 0


def recall(retrieved: set[int], relevant: set[int]) -> float:
    rel = len(relevant)
    return len(retrieved & relevant) / rel if rel != 0 else 0


def fbeta_score(retrieved: set[int], relevant: set[int], beta: float = 1.0) -> float:
    P = precision(retrieved, relevant)
    R = recall(retrieved, relevant)
    numerator = (beta**2 + 1) * P * R
    denominator = (beta**2 * P) + R
    return numerator / denominator if denominator != 0 else 0


def f1_score(retrieved: set[int], relevant: set[int]) -> float:
    return fbeta_score(retrieved, relevant, 1)


def precision_at_k(retrieved: list[int], relevant: set[int], k: int = 10) -> float:
    retrieved = retrieved[:k]
    return len(set(retrieved) & relevant) / k if k != 0 else 0


def recall_at_k(retrieved: list[int], relevant: set[int], k: int = 10) -> float:
    retrieved = retrieved[:k]
    return recall(set(retrieved), relevant)


def fbeta_score_at_k(
    retrieved: list[int], relevant: set[int], beta: float = 1.0, k: int = 10
) -> float:
    retrieved = retrieved[:k]
    P = len(set(retrieved) & relevant) / k if k != 0 else 0
    R = recall(set(retrieved), relevant)
    numerator = (beta**2 + 1) * P * R
    denominator = (beta**2 * P) + R
    return numerator / denominator if denominator != 0 else 0


def f1_score_at_k(retrieved: list[int], relevant: set[int], k):
    return fbeta_score_at_k(retrieved, relevant, 1, k)


def average_precision(retrieved: list[int], relevant: set[int]) -> float:
    relevant_num = 0
    ap = 0
    for i in range(len(retrieved)):
        if retrieved[i] in relevant:
            relevant_num += 1
            ap += relevant_num / (i + 1)
    return ap / len(relevant) if len(relevant) != 0 else 0


def MAP(retrieved: list[list], relevant: list[list]) -> float:
    """
    Retrieved and relevant should be of the same size which is the number of queries.
    Retrieved and relevant should each be a list of lists containing
    all retrieved lists for each query and all relevant lists for each query respectively.
    """
    ap_sum = 0
    for i in range(len(retrieved)):
        ap_sum += average_precision(retrieved[i], relevant[i])
    return ap_sum / len(retrieved) if len(retrieved) != 0 else 0


def jaccard_similarity(retrieved: set[int], relevant: set[int]) -> float:
    inter = len(retrieved & relevant)
    union = len(retrieved | relevant)
    return inter / union if union != 0 else 0


def jaccard_similarity_at_k(retrieved: list[int], relevant: set[int], k: int = 10) -> float:
    retrieved = retrieved[:k]
    return jaccard_similarity(set(retrieved), relevant)


def dcg(retrieved: list[int], relevant: graded_relevant_list) -> float:
    dcg_score = 0
    for i in range(len(retrieved)):
        if retrieved[i] in relevant:
            grade = relevant[retrieved[i]]
            dg = (2**grade - 1) / log2(i + 2)
            dcg_score += dg
    return dcg_score


def dcg_at_k(retrieved: list[int], relevant: graded_relevant_list, k: int = 10) -> float:
    retrieved = retrieved[:k]
    return dcg(retrieved, relevant)


def ideal_dcg(relevant: graded_relevant_list, k: int | None = None) -> float:
    sorted_relevant = sorted(relevant, key=relevant.get, reverse=True)
    if k is not None:
        sorted_relevant = sorted_relevant[:k]
    return dcg(sorted_relevant, relevant)


def normalized_dcg(retrieved: list[int], relevant: graded_relevant_list) -> float:
    dcg_score = dcg(retrieved, relevant)
    idcg_score = ideal_dcg(relevant)
    return dcg_score / idcg_score if idcg_score != 0 else 0


def normalized_dcg_at_k(retrieved: list[int], relevant: graded_relevant_list, k: int = 10) -> float:
    dcg_score = dcg_at_k(retrieved, relevant, k)
    idcg_score = ideal_dcg(relevant, k)
    return dcg_score / idcg_score if idcg_score != 0 else 0


def reciprocal_rank(retrieved: list[int], relevant: set[int]) -> float:
    for i, DocId in enumerate(retrieved):
        if DocId in relevant:
            return 1 / (i + 1)
    return 0.0


def mean_reciprocal_rank(retrieved: list[list[int]], relevant: list[set[int]]) -> float:
    return (
        sum(reciprocal_rank(ret, rel) for ret, rel in zip(retrieved, relevant)) / len(retrieved)
        if len(retrieved) != 0
        else 0
    )


def evaluate_query(retrieved: list[int], relevant: graded_relevant_list) -> pd.DataFrame:
    retrieved_set = set(retrieved)
    relevant_set = {hid for hid, grade in relevant.items() if grade > 0}
    return pd.DataFrame(
        [
            {
                "Precision": precision(retrieved_set, relevant_set),
                "Recall": recall(retrieved_set, relevant_set),
                "F1_Score": f1_score(retrieved_set, relevant_set),
                "AP": average_precision(retrieved, relevant_set),
                "IoU": jaccard_similarity(retrieved_set, relevant_set),
                "NDCG": normalized_dcg(retrieved, relevant),
            }
        ]
    )


def evaluate_query_at_k(retrieved: list[int], relevant: graded_relevant_list, k) -> pd.DataFrame:
    relevant_set = {hid for hid, grade in relevant.items() if grade > 0}
    return pd.DataFrame(
        [
            {
                f"Precision@{k}": precision_at_k(retrieved, relevant_set, k),
                f"Recall@{k}": recall_at_k(retrieved, relevant_set, k),
                f"F1_Score@{k}": f1_score_at_k(retrieved, relevant_set, k),
                f"IoU@{k}": jaccard_similarity_at_k(retrieved, relevant_set, k),
                f"nDCG@{k}": normalized_dcg_at_k(retrieved, relevant, k),
            }
        ]
    )


def evaluate_system(
    query_ids: list[str],
    retrieved_per_query: list[list[int]],
    relevant_per_query: list[graded_relevant_list],
    k: int = 20,
) -> pd.DataFrame:
    """Compute per-query IR metrics and return a DataFrame with a MEAN row."""
    rows = []
    for qid, retrieved, relevant in zip(query_ids, retrieved_per_query, relevant_per_query):
        relevant_set = {hid for hid, grade in relevant.items() if grade > 0}
        rows.append(
            {
                "query": qid,
                "AP": average_precision(retrieved, relevant_set),
                "RR": reciprocal_rank(retrieved, relevant_set),
                f"P@{k}": precision_at_k(retrieved, relevant_set, k),
                f"R@{k}": recall_at_k(retrieved, relevant_set, k),
                f"F1@{k}": f1_score_at_k(retrieved, relevant_set, k),
                f"nDCG@{k}": normalized_dcg_at_k(retrieved, relevant, k),
            }
        )
    df = pd.DataFrame(rows).set_index("query")
    df.loc["MEAN"] = df.mean()
    return df


def _filter_and_rank(scores: dict[int, float], eval_ids: set[int]) -> list[tuple[int, float]]:
    """Keep only eval-pool documents and return them sorted by descending score."""
    return sorted(
        ((doc_id, score) for doc_id, score in scores.items() if doc_id in eval_ids),
        key=lambda x: x[1],
        reverse=True,
    )


if __name__ == "__main__":
    from scripts.eval_pipeline import OutputNames, run_pipeline

    run_pipeline(OutputNames("qrels_results.json", "stats_results.json", "results_table.tex"))
