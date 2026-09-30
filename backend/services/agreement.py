"""Inter-annotator agreement (Cohen's kappa, Spearman rho, raw agreement) per query."""

from dataclasses import dataclass
from itertools import combinations

ROUND_DIGITS = 4
MIN_ANNOTATORS = 2
MIN_COMMON_LABELS = 2


@dataclass(frozen=True)
class QueryAgreement:
    entry: dict  # what the API returns for this query
    kappa: float | None = None  # unrounded, feeds the overall means
    spearman: float | None = None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, ROUND_DIGITS)


def _finite(values) -> list[float]:
    import numpy as np

    return [float(v) for v in values if v is not None and not np.isnan(v)]


def _pairwise_means(matrix) -> tuple[float | None, float | None]:
    """Mean kappa and Spearman rho over every pair of annotator rows."""
    from scipy.stats import spearmanr
    from sklearn.metrics import cohen_kappa_score

    pairs = list(combinations(range(len(matrix)), 2))
    kappas = _finite(cohen_kappa_score(matrix[i], matrix[j]) for i, j in pairs)
    rhos = _finite(spearmanr(matrix[i], matrix[j])[0] for i, j in pairs)
    return _mean(kappas), _mean(rhos)


def _raw_agreement(matrix) -> float:
    """Share of documents on which every annotator gave the same label."""
    return float((matrix == matrix[0]).all(axis=0).mean())


def _common_ids(pooled_ids: list[int], labels_by_annotator: dict[int, dict[int, int]]) -> list[int]:
    labelled_by_all = set.intersection(*(set(labels) for labels in labels_by_annotator.values()))
    return sorted(set(pooled_ids) & labelled_by_all)


def summarize_query(
    query_id: str,
    query_text: str,
    pooled_ids: list[int],
    labels_by_annotator: dict[int, dict[int, int]],
) -> QueryAgreement:
    import numpy as np

    base = {"query_id": query_id, "query": query_text, "annotators": len(labels_by_annotator)}
    if len(labels_by_annotator) < MIN_ANNOTATORS:
        return QueryAgreement({**base, "kappa": None, "agreement": None})

    common = _common_ids(pooled_ids, labels_by_annotator)
    if len(common) < MIN_COMMON_LABELS:
        return QueryAgreement(
            {**base, "common_labeled": len(common), "kappa": None, "agreement": None}
        )

    matrix = np.array([[labels[hid] for hid in common] for labels in labels_by_annotator.values()])
    kappa, spearman = _pairwise_means(matrix)
    entry = {
        **base,
        "common_labeled": len(common),
        "kappa": _round(kappa),
        "spearman": _round(spearman),
        "raw_agreement": round(_raw_agreement(matrix), ROUND_DIGITS),
    }
    return QueryAgreement(entry, kappa, spearman)


def overall_summary(results: list[QueryAgreement], total_queries: int) -> dict:
    kappas = [r.kappa for r in results if r.kappa is not None]
    spearmans = [r.spearman for r in results if r.spearman is not None]
    return {
        "mean_kappa": _round(_mean(kappas)),
        "mean_spearman": _round(_mean(spearmans)),
        "queries_with_agreement": len(kappas),
        "total_queries": total_queries,
    }
