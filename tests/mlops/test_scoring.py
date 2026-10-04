import math

import numpy as np
import pytest

from scripts import promote_model as pm
from scripts.promote_model import Candidate, Pair


def test_ndcg_perfect_and_hand_computed():
    assert pm.ndcg_at_k([2, 1, 0]) == pytest.approx(1.0)
    # (2 + 0 + 1/log2(4)) / (2 + 1/log2(3))
    assert pm.ndcg_at_k([2, 0, 1]) == pytest.approx(2.5 / (2 + 1 / math.log2(3)))
    assert pm.ndcg_at_k([0, 1, 2]) == pytest.approx((1 / math.log2(3) + 1) / (2 + 1 / math.log2(3)))


def test_ndcg_with_no_positive_label_is_zero_and_k_cuts_the_list():
    assert pm.ndcg_at_k([0, 0, 0]) == 0.0
    assert pm.ndcg_at_k([0, 1], k=1) == 0.0  # the relevant one is below the cut, ideal is 1.0
    assert pm.ndcg_at_k([1, 0], k=1) == 1.0


def _encoder(table):
    return lambda texts: np.array([table[t] for t in texts])


def _vec(*head):
    return list(head) + [0.0] * (64 - len(head))


def test_score_pairs_mean_over_queries_and_unit_cut():
    table = {
        "q1": _vec(1, 0),
        "q2": _vec(0, 1),
        "a": _vec(5, 0),  # length does not matter: vectors are made unit length
        "b": _vec(0, 3),
    }
    pairs = [
        Pair("q1", "a", 1),
        Pair("q1", "b", 0),  # q1 ranks a first: 1.0
        Pair("q2", "a", 1),
        Pair("q2", "b", 0),  # q2 ranks b first, the relevant a second: 1/log2(3)
    ]
    assert pm.score_pairs(pairs, _encoder(table)) == pytest.approx((1 + 1 / math.log2(3)) / 2)


def test_score_pairs_keeps_only_the_first_64_values():
    # the 65th value would change the ranking if it were not cut off
    table = {"q": _vec(1) + [100.0], "a": _vec(1) + [0.0], "b": _vec(0.5, 0.5) + [100.0]}
    pairs = [Pair("q", "a", 1), Pair("q", "b", 0)]
    assert pm.score_pairs(pairs, _encoder(table)) == 1.0


def test_score_pairs_refuses_short_vectors_and_all_zero_labels():
    with pytest.raises(pm.PromotionError, match="at least 64"):
        pm.score_pairs([Pair("q", "a", 1)], lambda texts: np.ones((len(texts), 8)))
    with pytest.raises(pm.PromotionError, match="positive label"):
        pm.score_pairs([Pair("q", "a", 0)], _encoder({}))


def test_decide_is_inclusive_at_the_margin():
    assert pm.decide(0.51, 0.50, 0.01)  # float noise must not decide this
    assert pm.decide(0.9, 0.5, 0.01)
    assert not pm.decide(0.509, 0.50, 0.01)
    assert not pm.decide(0.5, 0.6, 0.01)


def test_pick_best_prefers_score_then_newer_version():
    best = pm.pick_best([Candidate("2", None, 0.7), Candidate("10", None, 0.9)])
    assert best.version == "10"
    tie = pm.pick_best([Candidate("9", None, 0.8), Candidate("10", None, 0.8)])
    assert tie.version == "10"  # numeric, not text, order


def test_load_pairs_reads_the_format_and_rejects_others(tmp_path):
    good = tmp_path / "p.json"
    good.write_text('[{"query": "q", "text": "t", "label": 1, "hadith_id": 5}]')
    assert pm.load_pairs(str(good)) == [Pair("q", "t", 1.0)]
    for bad in ('{"a": 1}', '[{"query": "q"}]', "not json"):
        good.write_text(bad)
        with pytest.raises(pm.PromotionError, match="eval pairs file"):
            pm.load_pairs(str(good))


def test_find_model_dir_plain_and_flavor_layouts(tmp_path):
    plain = tmp_path / "plain" / "model"
    plain.mkdir(parents=True)
    (plain / "modules.json").write_text("[]")
    assert pm.find_model_dir(str(tmp_path / "plain")) == str(plain)
    flavor = tmp_path / "flavor" / "model" / "model"
    flavor.mkdir(parents=True)
    (tmp_path / "flavor" / "model" / "MLmodel").write_text("flavors: {}")
    (flavor / "modules.json").write_text("[]")
    (flavor / "config.json").write_text("{}")
    assert pm.find_model_dir(str(tmp_path / "flavor")) == str(flavor)
    (tmp_path / "empty").mkdir()
    with pytest.raises(pm.PromotionError, match="No sentence-transformers"):
        pm.find_model_dir(str(tmp_path / "empty"))
