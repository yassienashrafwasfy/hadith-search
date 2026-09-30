import threading
import time
from functools import lru_cache

from scripts.loading import _load_once


def test_racing_threads_load_only_once():
    calls = []

    @lru_cache()
    def slow_loader():
        calls.append(1)
        time.sleep(0.2)
        return object()

    load = _load_once(slow_loader)
    results = []
    threads = [threading.Thread(target=lambda: results.append(load())) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1
    assert len({id(r) for r in results}) == 1


def test_cache_clear_makes_the_next_call_load_again():
    calls = []

    @lru_cache()
    def loader():
        calls.append(1)
        return len(calls)

    load = _load_once(loader)
    assert load() == 1 and load() == 1
    load.cache_clear()
    assert load() == 2
