import numpy as np
import pytest
import requests

from scripts import search as s

pytestmark = pytest.mark.usefixtures("_mock_preprocess")


def test_term_overlap_ranks_by_matching_terms(_inverted_index):
    res = s.ranked_term_overlap("prayer fasting", "EN", _inverted_index)
    assert list(res) == [3, 1, 2]  # doc 3 matches both terms; ties broken by id
    assert res[3] == 2


def test_tf_idf_orders_and_skips_unknown_terms(_inverted_index, _document_lengths):
    res = s.tf_idf("shield unknownterm", "EN", _inverted_index, _document_lengths)
    assert list(res) == [2]


def test_bm25_prefers_docs_matching_more_terms(_inverted_index, _document_lengths):
    res = s.bm25("prayer fasting", "EN", _inverted_index, _document_lengths)
    assert next(iter(res)) == 3
    assert set(res) == {1, 2, 3}


def test_bm25_custom_weights_override_query(_inverted_index, _document_lengths):
    res = s.bm25("prayer", "EN", _inverted_index, _document_lengths, custom_weights={"shield": 1.0})
    assert list(res) == [2]


def test_hybrid_returns_all_matches(_inverted_index, _document_lengths):
    assert set(s.bm25_tfidf_hybrid("prayer", "EN", _inverted_index, _document_lengths)) == {1, 3}


def test_query_expansion_adds_new_terms(_inverted_index, _document_lengths):
    vec = s.query_expansion(
        "prayer", ["prayer pillar faith"], "EN", _inverted_index, _document_lengths, top_n=1
    )
    assert vec["prayer"] == 1.0
    assert len(vec) == 2  # original + 1 expansion term


def test_bm25_with_expansion_uses_hadith_lookup(_inverted_index, _document_lengths, _hadiths_df):
    def get_hadith(hid):
        return _hadiths_df.loc[hid]

    res = s.bm25_with_expansion(
        "prayer", "EN", _inverted_index, _document_lengths, get_hadith, k=1, top_n=1
    )
    assert res


def test_rrf_fusion_combines_lists():
    fused = s.rrf_fusion([{1: 1, 2: 2}, {2: 1, 3: 2}], k=60)
    assert next(iter(fused)) == 2
    assert fused[2] == pytest.approx(1 / 62 + 1 / 61)


def test_cosine_similarity_search_top_k(_embeddings, _hadith_ids):
    res = s.cosine_similarity_search(np.array([1.0, 0.0, 0.0]), _embeddings, _hadith_ids, top_k=2)
    assert list(res) == [1, 3]
    assert res[1] == pytest.approx(1.0)


def test_semantic_search_e5_uses_model(_embeddings, _hadith_ids, _fake_model):
    res = s.semantic_search_e5("anything", "EN", _fake_model, _embeddings, _hadith_ids, top_k=1)
    assert list(res) == [1]


def test_semantic_reranker_only_scores_candidates(_embeddings, _hadith_ids, _fake_model):
    res = s.semantic_reranker("q", "EN", [2, 3], _fake_model, _embeddings, _hadith_ids)
    assert set(res) == {2, 3}
    assert list(res)[0] == 3


def test_bm25_semantic_rrf(
    _inverted_index, _document_lengths, _embeddings, _hadith_ids, _fake_model
):
    res = s.bm25_semantic_rrf(
        "prayer", "EN", _inverted_index, _document_lengths, _embeddings, _hadith_ids, _fake_model
    )
    assert 1 in res


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._p, self.text = status, payload or {}, "err"

    def json(self):
        return self._p


def test_cross_encoder_requires_api_key(monkeypatch):
    monkeypatch.setattr(s, "JINA_API_KEY", None)
    with pytest.raises(RuntimeError, match="JINA_API_KEY"):
        s.cross_encoder_rerank("q", "EN", [1], {1: "text"})


def test_cross_encoder_empty_candidates_skips_api(_jina_env):
    assert s.cross_encoder_rerank("q", "EN", [9], {}) == {}


def test_cross_encoder_maps_indices_to_ids(_jina_env, monkeypatch):
    payload = {
        "results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]
    }
    calls = {}

    def fake_post(url, headers, json):
        calls.update(url=url, json=json)
        return _Resp(200, payload)

    monkeypatch.setattr(requests, "post", fake_post)
    res = s.cross_encoder_rerank("q", "EN", [10, 20], {10: "a", 20: "b"}, top_k=2)
    assert res == {20: 0.9, 10: 0.1}
    assert calls["json"]["documents"] == ["a", "b"]


def test_cross_encoder_http_error(_jina_env, monkeypatch):
    monkeypatch.setattr(requests, "post", lambda url, headers, json: _Resp(500))
    with pytest.raises(RuntimeError, match="status 500"):
        s.cross_encoder_rerank("q", "EN", [1], {1: "a"})


def test_final_pipeline(
    _jina_env,
    monkeypatch,
    _inverted_index,
    _document_lengths,
    _embeddings,
    _hadith_ids,
    _fake_model,
):
    monkeypatch.setattr(
        requests,
        "post",
        lambda url, headers, json: _Resp(200, {"results": [{"index": 0, "relevance_score": 0.5}]}),
    )
    res = s.final_search_pipeline(
        "prayer",
        "EN",
        _inverted_index,
        _document_lengths,
        _embeddings,
        _hadith_ids,
        _fake_model,
        eval_ids={1, 2, 3},
        texts_dict={1: "a", 2: "b", 3: "c"},
    )
    assert len(res) == 1
