import json

import pytest

from scripts import llm_validation as lv


def test_extract_pairs_matches_only_common_docs():
    human = {
        "q1": {"grades": {"1": 2, "2": 0, "3": 1}},
        "q2": {"grades": {}},
        "q3": {"grades": {"9": 1}},
    }
    llm = {"q1": {"1": 2, "2": 1}, "q2": {"1": 1}}
    pairs, per_query = lv.extract_pairs(human, llm)
    assert pairs == [(2, 2), (0, 1)]
    assert per_query == {"q1": [(2, 2), (0, 1)]}


def test_kappa_and_spearman_edge_cases():
    assert lv.compute_cohens_kappa([]) is None
    assert lv.compute_spearman([(1, 1)]) == (None, None)
    assert lv.compute_cohens_kappa([(0, 0), (1, 1), (2, 2)]) == 1.0
    rho, p = lv.compute_spearman([(0, 0), (1, 1), (2, 2)])
    assert rho == pytest.approx(1.0) and p < 0.05


def test_grade_distribution_formats_percentages():
    assert lv.compute_grade_distribution([0, 0, 1, 2]) == {
        0: "2 (50.0%)",
        1: "1 (25.0%)",
        2: "1 (25.0%)",
    }
    assert lv.compute_grade_distribution([]) == {0: 0, 1: 0, 2: 0}
    assert lv.compute_grade_distribution([5]) == {0: 0, 1: 0, 2: 0}


def test_per_query_stats():
    out = lv.compute_per_query_stats({"q1": [(0, 0), (1, 1), (2, 2)], "q2": [(1, 1)], "q3": []})
    assert out["q1"]["kappa"] == 1.0 and out["q1"]["agreement_rate"] == 1.0
    assert out["q1"]["spearman_rho"] == pytest.approx(1.0)
    assert out["q2"]["kappa"] is None and out["q2"]["spearman_rho"] is None
    assert out["q2"]["n_docs"] == 1
    assert out["q3"]["agreement_rate"] == 0


def test_per_query_constant_ratings_have_no_spearman():
    out = lv.compute_per_query_stats({"q": [(1, 1), (1, 1)]})
    assert out["q"]["spearman_rho"] is None and out["q"]["spearman_p"] is None
    assert out["q"]["agreement_rate"] == 1.0


@pytest.mark.parametrize(
    "kappa,text",
    [
        (None, "N/A"),
        (-0.1, "Less than chance agreement"),
        (0.1, "Slight agreement"),
        (0.3, "Fair agreement"),
        (0.5, "Moderate agreement"),
        (0.7, "Substantial agreement"),
        (0.9, "Almost perfect agreement"),
        (0.2, "Fair agreement"),
        (0.8, "Almost perfect agreement"),
    ],
)
def test_interpret_kappa(kappa, text):
    assert lv.interpret_kappa(kappa) == text


def _write(tmp_path, human, llm):
    h, m = tmp_path / "h.json", tmp_path / "l.json"
    h.write_text(json.dumps(human))
    m.write_text(json.dumps(llm))
    return str(h), str(m)


def test_validate_report(tmp_path):
    human = {"q1": {"grades": {"1": 0, "2": 1, "3": 2}}}
    llm = {"q1": {"1": 0, "2": 1, "3": 2}}
    h, m = _write(tmp_path, human, llm)
    report = lv.validate(h, m)
    assert report["metadata"] == {
        "n_queries_compared": 1,
        "n_total_pairs": 3,
        "human_qrels_path": h,
        "llm_grades_path": m,
    }
    assert report["overall"]["cohens_kappa"] == 1.0
    assert report["overall"]["agreement_rate"] == 1.0
    assert report["overall"]["spearman_p_value"] is not None
    assert report["kappa_interpretation"] == "Almost perfect agreement"
    assert set(report["per_query"]) == {"q1"}


def test_validate_reports_missing_overlap(tmp_path):
    h, m = _write(tmp_path, {"q1": {"grades": {"1": 1}}, "q2": {}}, {"q9": {"1": 1}, "q8": {}})
    assert lv.validate(h, m) == {
        "error": "No overlapping grades found between human and LLM qrels",
        "human_queries_with_grades": 1,
        "llm_queries_with_grades": 1,
    }


def test_latex_validation_table():
    report = {
        "per_query": {
            "EN_a_very_long_query_id": {
                "kappa": 0.5,
                "spearman_rho": None,
                "agreement_rate": 0.75,
                "n_docs": 4,
            }
        },
        "overall": {"cohens_kappa": None, "spearman_rho": 0.25, "agreement_rate": 0.5},
        "metadata": {"n_total_pairs": 4},
    }
    latex = lv.generate_latex_validation_table(report)
    assert "EN_a_very_lo & 4 & 0.500 & --- & 75.0%" in latex
    assert (
        r"\textbf{Overall} & \textbf{4} & \textbf{---} & \textbf{0.250} & \textbf{50.0%}" in latex
    )
