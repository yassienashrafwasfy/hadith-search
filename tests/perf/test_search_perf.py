"""Performance checks against a RUNNING app. Skipped unless RUN_PERF=1.

    RUN_PERF=1 PERF_BASE_URL=http://CONTAINER_IP:8000 .venv/bin/python -m pytest -n0 tests/perf -s

Point PERF_BASE_URL at the app container, not at nginx (nginx would rate-limit the test). Use
`-n0`: the tests time requests, and parallel test workers would disturb each other. Nothing else
heavy should run on the machine (mutation tests, builds), and the models should be warm: the
first test warms every method once.

The limits are the "Performance budgets" table of docs/HANDOFF.md (item 37). They are loose on
purpose (a few times the measured values) so the tests catch a real regression, such as an
index that stops being used or the encoder going back to many threads, and not noise.
"""

import os
import random
import re
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("PERF_BASE_URL", "http://127.0.0.1:8000")
HANDOFF = Path(__file__).resolve().parents[2] / "docs" / "HANDOFF.md"
ENGLISH = ["prayer in the mosque", "fasting in ramadan", "charity to the poor", "backbiting"]
ARABIC = ["حكم الصلاة في المسجد", "الصيام جنة", "فضل الصدقة", "بر الوالدين"]

pytestmark = pytest.mark.skipif(os.environ.get("RUN_PERF") != "1", reason="set RUN_PERF=1 to run")


def _budgets() -> dict[str, float]:
    """`| key | value |` rows under the "Performance budgets" heading of HANDOFF.md."""
    text = HANDOFF.read_text(encoding="utf-8")
    section = text.split("Performance budgets", 1)[1].split("\n#", 1)[0]
    rows = re.findall(r"^\|\s*`([^`]+)`\s*\|\s*([0-9.]+)\s*\|", section, re.M)
    return {key: float(value) for key, value in rows}


def _budget(budgets: dict[str, float], key: str, method: str | None = None) -> float:
    return budgets.get(f"{key}:{method}", budgets[key]) if method else budgets[key]


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1))))]


def _query(lang: str) -> str:
    return random.choice(ARABIC if lang == "ar" else ENGLISH)


def _search(client: httpx.Client, method: str, lang: str) -> tuple[int, float, httpx.Headers]:
    started = time.perf_counter()
    response = client.get(
        "/api/v1/searches", params={"q": _query(lang), "method": method, "lang": lang}
    )
    return response.status_code, (time.perf_counter() - started) * 1000, response.headers


@pytest.fixture(scope="module")
def _budget_table():
    return _budgets()


@pytest.fixture(scope="module")
def _client():
    if os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.skip("run with -n0: parallel workers disturb the timings")
    with httpx.Client(base_url=BASE_URL, timeout=60) as client:
        try:
            client.get("/api/v1/search-methods").raise_for_status()
        except httpx.HTTPError:
            pytest.skip(f"no app answering at {BASE_URL} (set PERF_BASE_URL)")
        yield client


@pytest.fixture(scope="module")
def _method_pairs(_client):
    """Every (method, language) the app offers, from /search-methods."""
    methods = _client.get("/api/v1/search-methods").json()["methods"]
    pairs = [(m["slug"], lang) for m in methods for lang in m["languages"]]
    assert len(pairs) >= 8
    for method, lang in pairs:  # warm: first calls load the encoder and the NLP models
        _search(_client, method, lang)
    return pairs


def test_single_user_latency_per_method(_client, _method_pairs, _budget_table):
    """One user, 20 requests per method and language: no errors, p95 inside the budget."""
    failures = []
    for method, lang in _method_pairs:
        results = [_search(_client, method, lang) for _ in range(20)]
        times = [ms for status, ms, _ in results if status == 200]
        errors = len(results) - len(times)
        p50, p95 = statistics.median(times), _percentile(times, 95)
        print(f"{method:18s} {lang} p50={p50:7.1f} p95={p95:7.1f} ms errors={errors}")
        limit = _budget(_budget_table, "single_user_p95_ms", method)
        if errors or p95 > limit:
            failures.append(
                f"{method} {lang}: errors={errors} p95={p95:.0f} ms (limit {limit:.0f})"
            )
    assert not failures, failures


def _user(deadline: float, pairs: list[tuple[str, str]], out: list) -> None:
    with httpx.Client(base_url=BASE_URL, timeout=60) as client:
        while time.monotonic() < deadline:
            out.append(_search(client, *random.choice(pairs)))
            time.sleep(random.uniform(0.5, 2))


def test_twenty_users_throughput(_client, _method_pairs, _budget_table):
    """20 users for 20 s, think time 0.5 to 2 s like the Locust mix: rps, p95 and no errors."""
    seconds, users, out = 20, 20, []
    deadline = time.monotonic() + seconds
    with ThreadPoolExecutor(users) as pool:
        for _ in range(users):
            pool.submit(_user, deadline, _method_pairs, out)
    times = [ms for status, ms, _ in out if status == 200]
    rps, p95 = len(out) / seconds, _percentile(times, 95)
    print(f"20 users: {len(out)} requests, rps={rps:.1f}, p95={p95:.0f} ms")
    assert all(status == 200 for status, _, _ in out)
    assert rps >= _budget(_budget_table, "users20_min_rps")
    assert p95 <= _budget(_budget_table, "users20_max_p95_ms")


def test_spike_is_queued_or_refused_cleanly(_client, _method_pairs, _budget_table):
    """150 requests at once: each is answered 200 or 503 with Retry-After, nothing else."""
    count, start = 150, threading.Barrier(150)

    def one(_):
        with httpx.Client(base_url=BASE_URL, timeout=60) as client:
            start.wait()
            return _search(client, "bm25", "en")

    with ThreadPoolExecutor(count) as pool:
        out = list(pool.map(one, range(count)))
    statuses = Counter(status for status, _, _ in out)
    print(f"spike statuses: {dict(statuses)}")
    assert set(statuses) <= {200, 503}
    assert statuses[200] > 0
    assert all("retry-after" in headers for status, _, headers in out if status == 503)
    assert statuses[503] / count <= _budget(_budget_table, "spike_max_refused_share")
