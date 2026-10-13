import pytest

from scripts.quran_eval import (
    evaluate,
    first_hit_rank,
    format_table,
    metrics,
    read_questions,
)


def test_first_hit_rank():
    assert first_hit_rank([(1, 1), (2, 2)], {(2, 2)}) == 2
    assert first_hit_rank([(1, 1)], {(9, 9)}) is None
    assert first_hit_rank([], {(1, 1)}) is None


def test_metrics_recall_and_mrr():
    ranked = [[(1, 1), (2, 2)], [(3, 3)], [(4, 4), (5, 5), (6, 6)]]
    golds = [{(2, 2)}, {(9, 9)}, {(4, 4)}]
    result = metrics(ranked, golds, ks=(1, 3))
    assert result["recall@1"] == pytest.approx(1 / 3)
    assert result["recall@3"] == pytest.approx(2 / 3)
    assert result["mrr"] == pytest.approx((1 / 2 + 0 + 1) / 3)


def test_metrics_of_nothing_is_zero():
    assert metrics([], [], ks=(1,)) == {"recall@1": 0.0, "mrr": 0.0}


def test_read_questions(tmp_path):
    path = tmp_path / "q.jsonl"
    path.write_text('{"question": "ما", "gold": [[2, 255]]}\n\n', encoding="utf-8")
    assert read_questions(str(path)) == [("ما", {(2, 255)})]


def test_evaluate_finds_the_ayah_by_its_own_text(
    _quran_session, _fake_encoder, _mini_corpus, monkeypatch
):
    from quran_support import _simple

    from scripts import arabic_encoder

    monkeypatch.setattr(arabic_encoder, "encoding_text", _simple)  # no CAMeL data needed
    target = next(a for a in _mini_corpus if (a.surah, a.ayah) == (112, 2))
    questions = [(target.text, {(112, 2)}), (_mini_corpus[0].text, {(1, 1)})]
    result = evaluate(_quran_session, _fake_encoder, "quran", questions, "dense")
    assert result["recall@1"] == 1.0 and result["mrr"] == 1.0


def test_format_table_lists_each_run():
    table = format_table(
        {"quran dense": {"recall@1": 0.5, "recall@3": 0.6, "recall@8": 0.7, "mrr": 0.55}}
    )
    assert "quran dense" in table and "0.700" in table
