"""Golden-output tests for the statistics/LaTeX generators and the LLM-validation report.

Snapshots were generated from an implementation verified identical to the pre-refactor one.
Regenerate deliberately with `UPDATE_SNAPSHOTS=1 pytest tests/test_snapshots.py -n0`.
"""

import pytest
from _snapshot import assert_matches

from scripts import llm_validation as lv
from scripts import stats_tests as st

_METRICS = ["AP", "RR", "P@20", "R@20", "F1@20", "nDCG@20"]
_SYSTEMS = ["BM25", "TF_IDF", "COSINE_SIMILARITY", "BM25_RRF", "BM25_ROCCHIO"]


def _results(offset, systems=_SYSTEMS, n=8):
    """Deterministic per-query metrics that differ by system, metric and query."""
    out = {}
    for si, system in enumerate(systems):
        block, sums = {}, dict.fromkeys(_METRICS, 0.0)
        for q in range(n):
            metrics = {
                m: round(
                    min(1.0, max(0.0, offset + 0.04 * si + 0.03 * ((q * (mi + 2) + si) % 7))), 4
                )
                for mi, m in enumerate(_METRICS)
            }
            block[f"q{q}"] = {"Query Text": f"query {q}", "Metrics": metrics}
            for m, v in metrics.items():
                sums[m] += v
        block["MEAN"] = {"Metrics": {m: v / n for m, v in sums.items()}}
        out[system] = block
    return out


@pytest.fixture(scope="module")
def _baseline():
    return _results(0.10)


@pytest.fixture(scope="module")
def _finetuned():
    return {
        "triplet": _results(0.22),
        "kv_pairs": _results(0.05, _SYSTEMS[:3]),
        "combined": _results(0.15),
    }


def test_pairwise_snapshot(_baseline):
    assert_matches("pairwise.json", st.run_pairwise_tests(_baseline))


def test_pairwise_no_baseline_snapshot(_baseline):
    small = {k: _baseline[k] for k in ("TF_IDF", "COSINE_SIMILARITY", "BM25_RRF")}
    assert_matches("pairwise_all_pairs.json", st.run_pairwise_tests(small, ["AP", "RR"]))


def test_latex_table_snapshot(_baseline):
    pairwise = st.run_pairwise_tests(_baseline)
    assert_matches("results_table.tex", st.generate_latex_table(_baseline, pairwise))


def test_cross_config_snapshot(_baseline, _finetuned):
    assert_matches("cross_config.json", st.run_cross_config_tests(_baseline, _finetuned))


def test_comparison_table_snapshot(_baseline, _finetuned):
    cross = st.run_cross_config_tests(_baseline, _finetuned)
    assert_matches(
        "comparison_table.tex", st.generate_comparison_latex_table(_baseline, _finetuned, cross)
    )


def test_delta_snapshots(_baseline, _finetuned):
    delta = st.generate_delta_table(_baseline, _finetuned)
    assert_matches("delta.json", delta)
    assert_matches("delta_table.tex", st.generate_delta_latex_table(delta))


def test_run_analysis_snapshot(_baseline):
    out = st.run_analysis(_baseline, n_bootstrap=200)
    out["metadata"].pop("timestamp")
    assert_matches("analysis.json", out)


def test_filter_graded_snapshot(_baseline):
    assert_matches("filtered.json", st.filter_graded_queries(_baseline, {"q1", "q4", "q6"}))


@pytest.mark.parametrize(
    "a,b",
    [
        ([0.9, 0.8, 0.7, 0.95, 0.85, 0.9], [0.1, 0.2, 0.15, 0.1, 0.2, 0.1]),
        ([0.5, 0.6, 0.4], [0.5, 0.6, 0.4]),
        ([0.5, 0.6], [0.4, 0.9]),
        ([0.5], [0.4]),
        ([0.5, 0.6, 0.7], [0.4]),
    ],
)
def test_two_sample_tests_snapshot(a, b):
    tag = f"{len(a)}_{len(b)}_{a[0]}"
    assert_matches(
        f"tests_{tag}.json", {"t": st.paired_t_test(a, b), "w": st.wilcoxon_signed_rank(a, b)}
    )


def test_bootstrap_snapshot():
    assert_matches("bootstrap.json", st.bootstrap_ci([0.1, 0.4, 0.35, 0.8, 0.5], n_bootstrap=300))


_HUMAN = {
    "q1": {"grades": {"1": 0, "2": 1, "3": 2, "4": 2, "5": 0}},
    "q2": {"grades": {"1": 1, "2": 1, "3": 1}},
    "q3": {"grades": {"7": 2, "8": 0}},
    "q4": {"grades": {}},
}
_LLM = {
    "q1": {"1": 0, "2": 2, "3": 2, "4": 1, "5": 0},
    "q2": {"1": 1, "2": 0, "3": 1},
    "q3": {"7": 2, "8": 0},
}


def test_llm_report_snapshot(tmp_path):
    import json

    h, m = tmp_path / "h.json", tmp_path / "l.json"
    h.write_text(json.dumps(_HUMAN))
    m.write_text(json.dumps(_LLM))
    report = lv.validate(str(h), str(m))
    report["metadata"].pop("human_qrels_path")
    report["metadata"].pop("llm_grades_path")
    assert_matches("llm_report.json", report)


def test_llm_latex_snapshot(tmp_path):
    import json

    h, m = tmp_path / "h.json", tmp_path / "l.json"
    h.write_text(json.dumps(_HUMAN))
    m.write_text(json.dumps(_LLM))
    assert_matches("llm_table.tex", lv.generate_latex_validation_table(lv.validate(str(h), str(m))))


@pytest.mark.parametrize("kappa", [None, -0.5, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0])
def test_interpret_kappa_boundaries_snapshot(kappa):
    assert_matches(f"kappa_{kappa}.json", lv.interpret_kappa(kappa))
