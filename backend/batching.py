"""Micro-batching for the query encoder.

Searches run in worker threads and each one encodes a single query. `MicroBatcher` collects the
queries that arrive within a few milliseconds and encodes them in one call, so the encoder runs
one batched pass instead of several single ones. A lone query waits at most `wait_seconds`.
Bulk calls (build and evaluation scripts) go straight through.
"""

import queue
import threading
import time
from concurrent.futures import Future

import numpy as np

RESULT_TIMEOUT_SECONDS = 60.0


class MicroBatcher:
    def __init__(self, encoder, wait_seconds: float, max_batch: int):
        self._encoder = encoder
        self._wait = wait_seconds
        self._max = max_batch
        self._pending: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._run, name="encoder-batcher", daemon=True)
        self._worker.start()

    def encode(self, texts: list[str], **kwargs) -> np.ndarray:
        if not texts or len(texts) > self._max or kwargs.get("batch_size"):
            return self._encoder.encode(texts, **kwargs)
        done = Future()
        self._pending.put((texts, done))
        return done.result(timeout=RESULT_TIMEOUT_SECONDS)

    def _collect(self) -> list:
        batch = [self._pending.get()]
        size = len(batch[0][0])
        deadline = time.monotonic() + self._wait
        while size < self._max:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                item = self._pending.get(timeout=remaining)
            except queue.Empty:
                break
            batch.append(item)
            size += len(item[0])
        return batch

    def _run(self) -> None:
        while True:
            batch = self._collect()
            self._answer(batch)

    def _answer(self, batch: list) -> None:
        try:
            vectors = self._encoder.encode([text for texts, _ in batch for text in texts])
        except Exception as exc:  # every caller in the batch gets the same failure
            for _, done in batch:
                done.set_exception(exc)
            return
        start = 0
        for texts, done in batch:
            done.set_result(vectors[start : start + len(texts)])
            start += len(texts)

    def __getattr__(self, name):
        return getattr(self._encoder, name)
