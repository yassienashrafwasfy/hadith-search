import pytest

from scripts import stats_tests as st


def _block(values, metrics=("AP", "RR")):
    """A results block for one system: per-query metrics plus MEAN."""
    block = {
        f"q{i}": {"Query Text": "t", "Metrics": {m: v for m in metrics}}
        for i, v in enumerate(values)
    }
    block["MEAN"] = {"Metrics": {m: sum(values) / len(values) for m in metrics}}
    return block


@pytest.fixture
def _results():
    return {
        "BM25": _block([0.1, 0.2, 0.3, 0.2, 0.1]),
        "GOOD": _block([0.6, 0.7, 0.8, 0.7, 0.9]),
        "BAD": _block([0.0, 0.1, 0.1, 0.0, 0.05]),
    }


def test_paired_t_test_insufficient_data():
    assert paired(([1.0], [1.0]))["note"] == "insufficient data"
    assert paired(([1.0, 2.0], [1.0]))["p_value"] is None


def paired(args):
    return st.paired_t_test(*args)


def test_paired_t_test_detects_difference():
    res = st.paired_t_test([0.9, 0.8, 0.85, 0.95, 0.9], [0.1, 0.2, 0.15, 0.1, 0.2])
    assert res["significant_at_0.01"] and res["significant_at_0.05"]
    assert res["t_statistic"] > 0


def test_wilcoxon_notes():
    assert st.wilcoxon_signed_rank([1], [1])["note"] == "insufficient data"
    same = st.wilcoxon_signed_rank([1, 2, 3], [1, 2, 3])
    assert same["p_value"] == 1.0 and same["note"] == "all differences are zero"
    assert same["significant_at_0.05"] is False


def test_wilcoxon_significant_and_failure(monkeypatch):
    res = st.wilcoxon_signed_rank(
        [0.9, 0.8, 0.7, 0.95, 0.85, 0.9, 0.99, 0.8], [0.1, 0.2, 0.15, 0.1, 0.2, 0.1, 0.3, 0.2]
    )
    assert res["test"] == "wilcoxon" and res["significant_at_0.05"]
    assert "note" not in res

    def boom(*a, **k):
        raise ValueError

    monkeypatch.setattr(st.scipy_stats, "wilcoxon", boom)
    assert st.wilcoxon_signed_rank([1, 2], [2, 4])["note"] == "test failed"


def test_test_result_nan_is_never_significant():
    res = st._test_result("x", "statistic", float("nan"), float("nan"))
    assert res["statistic"] is None and res["p_value"] is None
    assert res["significant_at_0.05"] is False


def test_direction():
    assert (st._direction(1), st._direction(-1), st._direction(0)) == ("better", "worse", "same")


def test_bootstrap_ci_degenerate_and_bounds():
    assert st.bootstrap_ci([]) == {"mean": 0.0, "ci_lower": 0.0, "ci_upper": 0.0, "std": 0.0}
    assert st.bootstrap_ci([0.5])["mean"] == 0.5
    ci = st.bootstrap_ci([0.1, 0.2, 0.3, 0.4], n_bootstrap=200)
    assert ci["ci_lower"] <= ci["mean"] <= ci["ci_upper"]


def test_pairwise_against_baseline(_results):
    out = st.run_pairwise_tests(_results, ["AP"], baseline="BM25")
    assert set(out["AP"]) == {"GOOD vs BM25", "BAD vs BM25"}
    assert out["AP"]["GOOD vs BM25"]["direction"] == "better"
    assert out["AP"]["BAD vs BM25"]["direction"] == "worse"
    assert out["AP"]["GOOD vs BM25"]["n_queries"] == 5


def test_pairwise_without_baseline_compares_all_pairs(_results):
    out = st.run_pairwise_tests(_results, ["AP"], baseline="missing")
    assert len(out["AP"]) == 3


def test_pairwise_skips_mismatched_lengths():
    res = {"BM25": _block([0.1, 0.2]), "X": _block([0.1, 0.2, 0.3])}
    assert st.run_pairwise_tests(res, ["AP"], baseline="BM25") == {"AP": {}}


def test_filter_graded_queries_recomputes_mean(_results):
    out = st.filter_graded_queries(_results, {"q0", "q1"})
    assert set(out["GOOD"]) == {"q0", "q1", "MEAN"}
    assert out["GOOD"]["MEAN"]["Metrics"]["AP"] == pytest.approx(0.65)


def test_run_analysis_summary(_results):
    out = st.run_analysis(_results, ["AP", "RR"], baseline="BM25", n_bootstrap=20)
    assert out["summary"]["best_systems"] == {"AP": "GOOD", "RR": "GOOD"}
    assert out["metadata"]["n_queries"] == 5
    assert out["metadata"]["systems"] == ["BM25", "GOOD", "BAD"]
    assert set(out["bootstrap_cis"]["GOOD"]) == {"AP", "RR"}


def test_run_analysis_empty():
    out = st.run_analysis({}, ["AP"], n_bootstrap=5)
    assert out["summary"]["best_systems"] == {} and out["metadata"]["n_queries"] == 0


def test_latex_table_bolds_best_and_marks_significance(_results):
    pairwise = st.run_pairwise_tests(_results, ["AP", "RR"], baseline="BM25")
    latex = st.generate_latex_table(_results, pairwise, ["AP", "RR"], baseline="BM25")
    assert r"\textbf{0.740}" in latex  # GOOD best AP
    assert r"\textbf{MAP}" in latex and r"\textbf{MRR}" in latex
    assert "GOOD" in latex and "3 systems" in latex
    good_row = next(line for line in latex.splitlines() if line.startswith("GOOD"))
    assert "*" in good_row
    bad_row = next(line for line in latex.splitlines() if line.startswith("BAD"))
    assert "*" not in bad_row  # worse than baseline is never starred
    assert latex.startswith(r"\begin{table*}[t]") and latex.endswith(r"\end{table*}")


def test_significance_marker():
    sig = {"t_test": {"p_value": 0.001, "significant_at_0.01": True}, "direction": "better"}
    assert st._significance_marker(sig) == "**"
    weak = {"t_test": {"p_value": 0.04, "significant_at_0.05": True}, "direction": "better"}
    assert st._significance_marker(weak) == "*"
    assert st._significance_marker({**sig, "direction": "worse"}) == ""
    assert st._significance_marker({}) == ""


def test_latex_escapes_underscores():
    assert st._escape("BM25_TF_IDF") == r"BM25\_TF\_IDF"


@pytest.fixture
def _ft_setup():
    base = {"COSINE_SIMILARITY": _block([0.2, 0.3, 0.25, 0.3, 0.2], ("AP",))}
    ft = {
        "triplet": {"COSINE_SIMILARITY": _block([0.6, 0.7, 0.65, 0.7, 0.6], ("AP",))},
        "kv_pairs": {"COSINE_SIMILARITY": _block([0.1, 0.1, 0.1, 0.1, 0.1], ("AP",))},
    }
    return base, ft


def test_cross_config_tests(_ft_setup):
    base, ft = _ft_setup
    out = st.run_cross_config_tests(base, ft, ["AP"], ["COSINE_SIMILARITY", "MISSING"])
    assert set(out) == {"COSINE_SIMILARITY"}
    entry = out["COSINE_SIMILARITY"]["AP"]["triplet vs baseline"]
    assert entry["direction"] == "better"
    assert entry["finetuned_mean"] > entry["baseline_mean"]
    assert out["COSINE_SIMILARITY"]["AP"]["kv_pairs vs baseline"]["direction"] == "worse"


def test_delta_table_and_averages(_ft_setup):
    base, ft = _ft_setup
    data = st.generate_delta_table(base, ft, ["AP"], ["COSINE_SIMILARITY"])
    triplet = data["per_system"]["COSINE_SIMILARITY"]["AP"]["triplet"]
    assert triplet["delta_pct"] == pytest.approx(160.0)
    assert data["averages"]["AP"]["kv_pairs"] < 0
    assert "combined" not in data["averages"]["AP"]


def test_delta_entry_zero_baseline():
    assert st._delta_entry(0.0, 0.5)["delta_pct"] == 0.0


def test_comparison_and_delta_latex(_ft_setup):
    base, ft = _ft_setup
    cross = st.run_cross_config_tests(base, ft, ["AP"], ["COSINE_SIMILARITY"])
    table = st.generate_comparison_latex_table(base, ft, cross, ["AP"], ["COSINE_SIMILARITY"])
    rows = [line for line in table.splitlines() if line.startswith((" &", "COSINE"))]
    assert len(rows) == 3  # Baseline + triplet + kv_pairs; combined has no results
    assert "FT(triplet)" in rows[1] and "FT(kv)" in rows[2]
    assert table.count(r"\midrule") == 1  # trailing separator removed
    delta = st.generate_delta_latex_table(
        st.generate_delta_table(base, ft, ["AP"], ["COSINE_SIMILARITY"]), ["AP"]
    )
    assert "+160.0\\%" in delta and r"\textbf{Average}" in delta


def test_delta_latex_empty_when_no_modes():
    assert st.generate_delta_latex_table({"averages": {"AP": {}}, "per_system": {}}, ["AP"]) == ""
