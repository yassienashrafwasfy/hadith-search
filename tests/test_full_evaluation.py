import json

import pytest

from scripts import full_evaluation as fe

_SYSTEMS = ["BM25", "COSINE_SIMILARITY", "BM25_RRF"]


def _block(values):
    block = {
        f"q{i}": {"Query Text": "t", "Metrics": {m: v for m in fe.METRICS}}
        for i, v in enumerate(values)
    }
    block["MEAN"] = {"Metrics": {m: sum(values) / len(values) for m in fe.METRICS}}
    return block


def _results(shift):
    return {s: _block([min(1, shift + 0.05 * i) for i in range(6)]) for s in _SYSTEMS}


@pytest.fixture
def _data(tmp_path, monkeypatch):
    monkeypatch.setattr(fe, "DATA_DIR", str(tmp_path))
    return tmp_path


def _dump(path, data):
    path.write_text(json.dumps(data))


def _graded(n=6):
    return {f"q{i}": {"grades": {"1": 1}} for i in range(n)} | {"q_ungraded": {"grades": {}}}


def test_load_results_missing_and_present(tmp_path):
    assert fe.load_results(str(tmp_path / "nope.json")) is None
    _dump(tmp_path / "r.json", {"a": 1})
    assert fe.load_results(str(tmp_path / "r.json")) == {"a": 1}


def test_main_stops_without_baseline(_data, capsys):
    fe.main([])
    assert "Baseline results not found" in capsys.readouterr().out
    assert not list(_data.glob("*.tex"))


def test_main_stops_without_qrels(_data, capsys):
    _dump(_data / "qrels_results.json", _results(0.1))
    fe.main([])
    assert "Graded qrels not found" in capsys.readouterr().out
    assert not (_data / "stats_results.json").exists()


def test_main_baseline_only(_data, capsys):
    _dump(_data / "qrels_results.json", _results(0.1))
    _dump(_data / "qrels_graded.json", _graded())
    fe.main(["--k", "20"])
    out = capsys.readouterr().out
    assert "WARNING: Fine-tuned results not found for 'triplet'" in out
    assert "No fine-tuned results found. Done." in out
    stats = json.loads((_data / "stats_results.json").read_text())
    assert stats["metadata"]["n_queries"] == 6 and stats["metadata"]["baseline"] == "BM25"
    assert (_data / "results_table.tex").read_text().startswith(r"\begin{table*}")
    assert not (_data / "comparison_results.json").exists()


def test_main_full_run(_data, capsys):
    _dump(_data / "qrels_results.json", _results(0.1))
    _dump(_data / "qrels_graded.json", _graded())
    _dump(_data / "finetuned_results_triplet.json", _results(0.3))
    _dump(_data / "finetuned_results_combined.json", _results(0.2))
    fe.main(["--baseline", "BM25"])
    out = capsys.readouterr().out
    assert "Fine-tuned loaded: triplet" in out and "SUMMARY" in out
    assert "Best fine-tuning mode per system" in out
    comparison = json.loads((_data / "comparison_results.json").read_text())
    assert comparison["metadata"]["modes"] == ["triplet", "combined"]
    assert comparison["metadata"]["n_graded_queries"] == 6
    assert set(comparison["cross_config_tests"]) == {"COSINE_SIMILARITY", "BM25_RRF"}
    assert set(comparison["delta"]) == {"per_system", "averages"}
    assert (_data / "comparison_table.tex").exists() and (_data / "delta_table.tex").exists()
    archived = [p.name for p in _data.glob("*_20*")]
    assert len(archived) == 3  # comparison table, delta table, comparison json


def test_mode_deltas_and_format():
    delta = {
        "averages": {m: {"triplet": 1.0, "kv_pairs": 1.0} for m in fe.METRICS},
        "per_system": {
            "S": {
                m: {"triplet": {"delta_pct": 10.0}, "kv_pairs": {"delta_pct": -4.0}}
                for m in fe.METRICS
            }
        },
    }
    deltas = fe._mode_deltas(delta, "S")
    assert deltas == {"triplet": 10.0, "kv_pairs": -4.0}
    assert fe._format_deltas(deltas) == "triplet: +10.0%  kv_pairs: -4.0%"
    assert fe._mode_deltas(delta, "unknown") == {}


def test_count_comparisons():
    assert fe._count_comparisons({"S": {"AP": {"a": 1, "b": 2}, "RR": {"a": 1}}, "T": {}}) == 3


def test_archive_writes_json_and_text(tmp_path):
    fe._archive({str(tmp_path / "a.json"): {"x": 1}, str(tmp_path / "b.tex"): "tex"}, "TS")
    assert json.loads((tmp_path / "a_TS.json").read_text()) == {"x": 1}
    assert (tmp_path / "b_TS.tex").read_text() == "tex"
