import json

import pandas as pd
import pytest

from scripts import eval_pipeline as ep


def _frame(k=20):
    cols = ["AP", "RR", "P@20", "R@20", "F1@20", f"nDCG@{k}"]
    df = pd.DataFrame(
        [[0.5, 1.0, 0.1, 0.2, 0.3, 0.4], [0.7, 0.5, 0.2, 0.3, 0.4, 0.6]],
        index=["q1", "MEAN"],
        columns=cols,
    )
    return df


def test_system_block_shape():
    block = ep._system_block(_frame(), ["q1"], ["query one"], 20)
    assert block["q1"]["Query Text"] == "query one"
    assert block["q1"]["Metrics"] == {
        "AP": 0.5,
        "RR": 1.0,
        "P@20": 0.1,
        "R@20": 0.2,
        "F1@20": 0.3,
        "nDCG@20": 0.4,
    }
    assert block["MEAN"]["Metrics"]["nDCG@20"] == 0.6
    assert "Query Text" not in block["MEAN"]


def test_system_block_uses_k_in_ndcg_name():
    block = ep._system_block(_frame(k=10), ["q1"], ["q"], 10)
    assert "nDCG@10" in block["MEAN"]["Metrics"]


def test_stamp_inserts_timestamp_before_extension():
    assert ep._stamp("/d/x.y/results.json", "T") == "/d/x.y/results_T.json"


def test_load_eval_inputs_and_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(ep, "DATA_DIR", str(tmp_path))
    (tmp_path / "queries.json").write_text(json.dumps({"EN1": "prayer", "AR1": "صلاة", "EN2": "z"}))
    (tmp_path / "qrels_graded.json").write_text(
        json.dumps({"EN1": {"grades": {"1": 2, "2": 0}}, "AR1": {"grades": {"3": 1}}})
    )
    ids, queries, relevant, langs = ep.load_eval_inputs()
    assert ids == ["EN1", "AR1", "EN2"] and queries == ["prayer", "صلاة", "z"]
    assert relevant == [{1: 2, 2: 0}, {3: 1}, {}]
    assert langs == ["EN", "AR", "EN"]
    assert ep.eval_pool_ids(relevant) == {1, 2, 3}
    (tmp_path / "qrels_ungraded.json").write_text(json.dumps({"EN1": [7, "8"]}))
    assert ep.eval_pool_ids(relevant) == {1, 2, 3, 7, 8}


def test_simulated_pipeline_rejects_unknown_type():
    with pytest.raises(ValueError, match="Unknown model_type: nope"):
        ep.simulated_pipeline(None, "q", "EN", "nope")


def test_save_outputs_writes_and_archives(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ep, "DATA_DIR", str(tmp_path))
    names = ep.OutputNames("r.json", "s.json", "t.tex")
    ep.save_outputs(names, {"a": 1}, {"b": 2}, "latex")
    assert json.loads((tmp_path / "r.json").read_text()) == {"a": 1}
    assert json.loads((tmp_path / "s.json").read_text()) == {"b": 2}
    assert (tmp_path / "t.tex").read_text() == "latex"
    stamped = sorted(p.name for p in tmp_path.iterdir() if p.name.count("_") == 2)
    assert len(stamped) == 3
    assert "r" in capsys.readouterr().out


def test_print_best_systems(capsys):
    ep.print_best_systems(
        {"summary": {"best_systems": {"AP": "GOOD"}}}, {"GOOD": {"MEAN": {"Metrics": {"AP": 0.5}}}}
    )
    out = capsys.readouterr().out
    assert "AP          : GOOD (0.5000)" in out


class _Res:
    """Minimal EvalResources stand-in for the simulated pipelines."""

    session = "session"
    model = "model"
    eval_ids = {1, 3}


def test_bm25_candidates_are_restricted_to_the_eval_pool(monkeypatch):
    seen = {}

    def fake(session, query, lang, restrict, limit):
        seen.update(session=session, query=query, lang=lang, restrict=restrict, limit=limit)
        return {3: 5.0, 1: 2.0}

    monkeypatch.setattr(ep.ranking, "bm25", fake)
    assert ep._bm25_candidates(_Res, "prayer", "EN") == [3, 1]
    assert seen == {
        "session": "session",
        "query": "prayer",
        "lang": "EN",
        "restrict": {1, 3},
        "limit": ep.BM25_CANDIDATES,
    }


def test_cross_encoder_gets_the_bm25_candidates(monkeypatch):
    seen = {}
    monkeypatch.setattr(ep.ranking, "bm25", lambda *a, **k: {3: 5.0, 1: 2.0})

    def fake(session, query, lang, ids, top_k):
        seen.update(ids=ids, top_k=top_k)
        return {1: 1.0}

    monkeypatch.setattr(ep.ranking, "cross_encode", fake)
    assert ep.simulated_pipeline(_Res, "prayer", "EN", "cross-encoder") == {1: 1.0}
    assert seen == {"ids": [3, 1], "top_k": 100}


def test_every_system_is_registered():
    assert set(ep.build_systems(_Res)) == {
        "BM25",
        "TF_IDF",
        "Term Overlap",
        "BM25_ROCCHIO",
        "BM25_TF_IDF",
        "BM25_TF_IDF_ROCCHIO",
        "COSINE_SIMILARITY",
        "BM25_SEMANTIC_RERANK",
        "BM25_RRF",
        "BM25_CROSS_ENCODER",
        "FINAL_PIPELINE",
    }
