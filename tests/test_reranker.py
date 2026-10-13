import pytest

from services.reranker import CrossEncoderReranker, plain, score_pairs

MAX_PASSAGE_CHARS = 400


class _Model:
    def __init__(self, raw):
        self.raw, self.pairs = raw, None

    def predict(self, pairs):
        self.pairs = pairs
        return self.raw


def test_plain_removes_marks_and_normalises_alef_wasla():
    assert plain("ٱللَّهُ  لَآ إِلَٰهَ") == "الله لآ إله"


def test_score_pairs_cleans_both_sides_and_keeps_the_model_scores():
    model = _Model([0.02, 0.9])
    scores = score_pairs(model, "ٱلصَّبْر؟", ["ٱلْحَمْدُ", "x" * 1000])
    assert model.pairs[0] == ("الصبر؟", "الحمد")
    assert len(model.pairs[1][1]) == MAX_PASSAGE_CHARS
    assert scores == [0.02, 0.9]


async def test_empty_input_never_loads_the_model(monkeypatch):
    def boom(name):
        raise AssertionError("loaded")

    monkeypatch.setattr("services.reranker.load_cross_encoder", boom)
    assert await CrossEncoderReranker("m")("q", []) == []


async def test_reranker_scores_in_a_worker_thread(monkeypatch):
    monkeypatch.setattr("services.reranker.load_cross_encoder", lambda name: _Model([0.7]))
    assert await CrossEncoderReranker("m")("q", ["t"]) == pytest.approx([0.7])


def test_rerank_texts_loads_the_model_once_and_applies_the_char_limit(monkeypatch):
    from services import reranker

    model = _Model([0.3, 0.8])
    monkeypatch.setattr(reranker, "load_cross_encoder", lambda name: model)
    assert reranker.rerank_texts("m", "q", ["a", "b" * 50], max_chars=10) == [0.3, 0.8]
    assert len(model.pairs[1][1]) == 10
    assert reranker.rerank_texts("m", "q", []) == []


def test_cross_rerank_orders_by_score_and_cuts(_search_index, _db_session):
    from services import ranking

    seen = {}

    def rerank(query, texts):
        seen["texts"] = texts
        return [0.1, 0.9, 0.5][: len(texts)]

    out = ranking.cross_rerank(_db_session, "q", [1, 2, 3], rerank, top_k=2)
    assert list(out) == [2, 3] and out[2] == 0.9
    assert all(isinstance(t, str) and t for t in seen["texts"])


def test_cross_rerank_without_candidates_returns_nothing(_search_index, _db_session):
    from services import ranking

    assert ranking.cross_rerank(_db_session, "q", [999], lambda q, t: [1.0], top_k=5) == {}
