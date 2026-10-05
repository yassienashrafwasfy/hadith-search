import threading

import numpy as np
import pytest
from sqlalchemy.pool import NullPool

import database
from batching import MicroBatcher
from settings import get_settings


class _Encoder:
    """Records every call; a vector is the text length, so each caller can check its own row."""

    dim = 3

    def __init__(self, fail=False):
        self.calls: list[list[str]] = []
        self.fail = fail

    def encode(self, texts, **_kw):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("encoder broke")
        return np.array([[len(t)] * self.dim for t in texts], dtype=float)


def _run_together(batcher, texts):
    results, start = {}, threading.Barrier(len(texts))

    def call(text):
        start.wait()
        results[text] = batcher.encode([text])

    threads = [threading.Thread(target=call, args=(t,)) for t in texts]
    [t.start() for t in threads]
    [t.join(5) for t in threads]
    return results


def test_concurrent_queries_share_one_encoder_call_and_get_their_own_row():
    encoder = _Encoder()
    batcher = MicroBatcher(encoder, wait_seconds=0.2, max_batch=16)
    texts = ["a", "bb", "ccc", "dddd"]
    results = _run_together(batcher, texts)
    assert sum(len(c) for c in encoder.calls) == 4 and len(encoder.calls) < 4
    for text in texts:
        assert results[text].shape == (1, 3) and results[text][0][0] == len(text)


def test_a_lone_query_waits_only_the_batch_window():
    encoder = _Encoder()
    batcher = MicroBatcher(encoder, wait_seconds=0.01, max_batch=16)
    assert batcher.encode(["abc"])[0][0] == 3
    assert encoder.calls == [["abc"]]


def test_the_batch_is_closed_when_it_is_full():
    encoder = _Encoder()
    batcher = MicroBatcher(encoder, wait_seconds=5, max_batch=2)
    results = _run_together(batcher, ["a", "bb"])  # would wait 5 s if the batch stayed open
    assert len(results) == 2 and encoder.calls[0] and len(encoder.calls[0]) == 2


def test_bulk_and_empty_calls_bypass_the_batcher():
    encoder = _Encoder()
    batcher = MicroBatcher(encoder, wait_seconds=5, max_batch=2)
    assert batcher.encode(["a", "b", "c"]).shape == (3, 3)
    assert batcher.encode(["x"], batch_size=8).shape == (1, 3)
    assert encoder.calls[0] == ["a", "b", "c"]


def test_an_encoder_failure_reaches_every_caller_and_the_batcher_keeps_working():
    encoder = _Encoder(fail=True)
    batcher = MicroBatcher(encoder, wait_seconds=0.1, max_batch=16)
    with pytest.raises(RuntimeError, match="encoder broke"):
        batcher.encode(["a"])
    encoder.fail = False
    assert batcher.encode(["abc"])[0][0] == 3


def test_other_attributes_come_from_the_wrapped_encoder():
    batcher = MicroBatcher(_Encoder(), wait_seconds=0.01, max_batch=2)
    assert batcher.dim == 3


def test_pool_is_sized_from_the_search_limit(monkeypatch):
    monkeypatch.setattr(database, "ENGINE_KWARGS", {"pool_pre_ping": True})
    monkeypatch.setenv("SEARCH_MAX_CONCURRENT", "6")
    monkeypatch.setenv("DB_POOL_OVERFLOW", "1")
    monkeypatch.setenv("DB_POOL_RECYCLE_SECONDS", "60")
    get_settings.cache_clear()
    assert database.pool_kwargs() == {
        "pool_pre_ping": True,
        "pool_size": 6,
        "max_overflow": 1,
        "pool_recycle": 60,
    }
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    get_settings.cache_clear()
    assert database.pool_kwargs()["pool_size"] == 3


def test_a_custom_pool_class_is_left_alone(monkeypatch):
    monkeypatch.setattr(database, "ENGINE_KWARGS", {"poolclass": NullPool})
    assert database.pool_kwargs() == {"poolclass": NullPool}


def test_get_model_batches_only_when_a_wait_is_set(monkeypatch):
    from scripts import arabic_encoder, loading

    fake = _Encoder()
    monkeypatch.setattr(arabic_encoder, "load_encoder", lambda *a, **k: fake)
    loading.get_model.cache_clear()
    monkeypatch.delenv("ENCODER_BATCH_WAIT_MS", raising=False)
    get_settings.cache_clear()
    assert loading.get_model() is fake
    loading.get_model.cache_clear()
    monkeypatch.setenv("ENCODER_BATCH_WAIT_MS", "5")
    get_settings.cache_clear()
    assert isinstance(loading.get_model(), MicroBatcher)
    loading.get_model.cache_clear()
