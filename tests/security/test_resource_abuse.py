"""Resource abuse: the search queue under load, and the size caps that bound one request."""

import asyncio
import threading
import time

import pytest

import routers.search as search_module
from security._helpers import API, assert_clean

SEARCH = f"{API}/searches"
PARAMS = {"q": "prayer", "method": "bm25"}


@pytest.fixture
def _slow_search(monkeypatch):
    """Make every search take `delay` seconds, and count how many run at once."""
    state = {
        "delay": 0.3,
        "running": 0,
        "peak": 0,
        "entered": threading.Event(),
        "lock": threading.Lock(),
    }
    real = search_module.run_search

    def slow(*args, **kwargs):
        with state["lock"]:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
        state["entered"].set()
        try:
            time.sleep(state["delay"])
            return real(*args, **kwargs)
        finally:
            with state["lock"]:
                state["running"] -= 1

    monkeypatch.setattr(search_module, "run_search", slow)
    return state


async def test_a_full_queue_answers_503_with_retry_after(_make_app, _open, _slow_search):
    _slow_search["delay"] = 1.0
    app = _make_app(app_env="test", search_max_concurrent=1, search_queue_size=0)
    async with _open(app) as api:
        first = asyncio.create_task(api.get(SEARCH, params=PARAMS))
        while not _slow_search["entered"].is_set():
            await asyncio.sleep(0.01)
        refused = await api.get(SEARCH, params=PARAMS)
        assert refused.status_code == 503
        assert int(refused.headers["retry-after"]) >= 1
        assert_clean(refused, allow=[503])
        assert (await first).status_code == 200
        assert (await api.get(SEARCH, params=PARAMS)).status_code == 200  # recovered


async def test_a_waiter_that_waits_too_long_gets_503(_make_app, _open, _slow_search):
    _slow_search["delay"] = 1.5
    app = _make_app(
        app_env="test",
        search_max_concurrent=1,
        search_queue_size=5,
        search_queue_timeout_seconds=0.3,
    )
    async with _open(app) as api:
        first = asyncio.create_task(api.get(SEARCH, params=PARAMS))
        while not _slow_search["entered"].is_set():
            await asyncio.sleep(0.01)
        late = await api.get(SEARCH, params=PARAMS)
        assert late.status_code == 503 and "retry-after" in late.headers
        assert (await first).status_code == 200


async def test_a_burst_gets_only_200_or_503_and_never_runs_more_than_allowed(
    _make_app, _open, _slow_search
):
    app = _make_app(
        app_env="test",
        search_max_concurrent=2,
        search_queue_size=3,
        search_queue_timeout_seconds=0.5,
    )
    async with _open(app) as api:
        answers = await asyncio.gather(*[api.get(SEARCH, params=PARAMS) for _ in range(40)])
        codes = [a.status_code for a in answers]
        assert set(codes) <= {200, 503}
        assert codes.count(200) >= 2 and codes.count(503) >= 20
        assert all("retry-after" in a.headers for a in answers if a.status_code == 503)
        assert _slow_search["peak"] <= 2
        assert (await api.get(SEARCH, params=PARAMS)).status_code == 200  # and it recovers


async def test_other_routes_still_answer_while_the_search_queue_is_full(
    _make_app, _open, _slow_search
):
    _slow_search["delay"] = 1.0
    app = _make_app(app_env="test", search_max_concurrent=1, search_queue_size=0)
    async with _open(app) as api:
        first = asyncio.create_task(api.get(SEARCH, params=PARAMS))
        while not _slow_search["entered"].is_set():
            await asyncio.sleep(0.01)
        assert (await api.get(f"{API}/hadiths/1")).status_code == 200
        assert (await api.get(f"{API}/search-methods")).status_code == 200
        await first


async def test_a_rejected_search_does_no_work_and_does_not_leak_a_slot(
    _make_app, _open, _slow_search
):
    _slow_search["delay"] = 0.05
    app = _make_app(app_env="test", search_max_concurrent=1, search_queue_size=0)
    async with _open(app) as api:
        for _ in range(5):  # invalid requests fail before a search runs
            assert (
                await api.get(SEARCH, params={"q": "x" * 501, "method": "bm25"})
            ).status_code == 422
            assert (await api.get(SEARCH, params={"q": "x", "method": "nope"})).status_code == 422
        for _ in range(3):
            assert (await api.get(SEARCH, params=PARAMS)).status_code == 200
    assert app.state.search_limiter.waiting == 0


# ---------- caps that bound one request ----------


async def test_every_cap_that_bounds_a_request_is_in_force(_api, _alice):
    h = _alice["headers"]
    assert (await _api.get(SEARCH, params={"q": "a" * 501, "method": "bm25"})).status_code == 422
    assert (await _api.get(f"{API}/kv-pairs", params={"limit": 201}, headers=h)).status_code == 422
    for field, size in (("username", 65), ("password", 129)):
        body = {"username": "abc", "password": PASSWORD_OK} | {field: "x" * size}
        assert (await _api.post(f"{API}/tokens", json=body)).status_code == 422
        assert (await _api.post(f"{API}/annotators", json=body)).status_code == 422


PASSWORD_OK = "long-enough-1"


async def test_the_queue_settings_must_be_positive_numbers(_make_app):
    from pydantic import ValidationError

    for name in ("search_max_concurrent", "search_queue_timeout_seconds"):
        for bad in (0, -1):
            with pytest.raises(ValidationError):
                _make_app(**{name: bad})
    with pytest.raises(ValidationError):
        _make_app(search_queue_size=-1)
