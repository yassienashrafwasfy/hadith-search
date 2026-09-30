import pytest
import requests

from scripts import search as s


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


def test_rrf_fusion_combines_lists():
    fused = s.rrf_fusion([{1: 1, 2: 2}, {2: 1, 3: 2}], k=60)
    assert next(iter(fused)) == 2
    assert fused[2] == pytest.approx(1 / 62 + 1 / 61)
