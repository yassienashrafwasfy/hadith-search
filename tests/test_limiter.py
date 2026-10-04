import asyncio

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from limiter import Overloaded, SearchLimiter, search_slot
from main import create_app
from rest import install_error_handlers
from routers.search import make_search_router
from settings import Settings


async def _hold(limiter, started: list, release: asyncio.Event):
    async with limiter.slot():
        started.append(1)
        await release.wait()


async def _wait_until(condition, tries=200):
    for _ in range(tries):
        if condition():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached")


async def test_runs_up_to_the_limit_and_queues_the_rest_in_order():
    limiter, release, order = SearchLimiter(2, 3, 5), asyncio.Event(), []

    async def job(n):
        async with limiter.slot():
            order.append(n)
            await release.wait()

    tasks = [asyncio.create_task(job(n)) for n in range(5)]
    await _wait_until(lambda: len(order) == 2 and limiter.waiting == 3)
    assert order == [0, 1]
    release.set()
    await asyncio.gather(*tasks)
    assert order == [0, 1, 2, 3, 4] and limiter.waiting == 0


async def test_a_full_queue_refuses_at_once():
    limiter, release, started = SearchLimiter(1, 1, 5), asyncio.Event(), []
    first = asyncio.create_task(_hold(limiter, started, release))
    await _wait_until(lambda: started)
    second = asyncio.create_task(_hold(limiter, started, release))
    await _wait_until(lambda: limiter.waiting == 1)
    with pytest.raises(Overloaded, match="queue full"):
        async with limiter.slot():
            pass
    release.set()
    await asyncio.gather(first, second)


async def test_a_waiter_gives_up_after_the_timeout_and_frees_its_place():
    limiter, release, started = SearchLimiter(1, 4, 0.05), asyncio.Event(), []
    holder = asyncio.create_task(_hold(limiter, started, release))
    await _wait_until(lambda: started)
    with pytest.raises(Overloaded, match="too long"):
        async with limiter.slot():
            pass
    assert limiter.waiting == 0
    release.set()
    await holder
    async with limiter.slot():  # the slot is free again
        pass


async def test_a_slot_is_released_when_the_work_raises():
    limiter = SearchLimiter(1, 0, 1)
    with pytest.raises(ValueError):
        async with limiter.slot():
            raise ValueError
    async with limiter.slot():
        pass


def _app(limiter):
    app = FastAPI()
    install_error_handlers(app)
    app.state.search_limiter = limiter
    gate = asyncio.Event()
    app.state.gate = gate

    @app.get("/work", dependencies=[Depends(search_slot)])
    async def work():
        await gate.wait()
        return {"ok": True}

    return app


async def test_route_answers_503_with_retry_after_when_the_queue_is_full():
    app = _app(SearchLimiter(1, 0, 2.2))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        running = asyncio.create_task(client.get("/work"))
        await asyncio.sleep(0.05)
        refused = await client.get("/work")
        assert refused.status_code == 503
        assert refused.headers["retry-after"] == "3"
        assert refused.headers["content-type"].startswith("application/problem+json")
        assert refused.json()["status"] == 503
        app.state.gate.set()
        assert (await running).status_code == 200
        assert (await client.get("/work")).status_code == 200


async def test_no_limiter_means_no_limit():
    app = _app(None)
    app.state.gate.set()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        assert (await client.get("/work")).status_code == 200


def test_the_search_route_uses_the_limiter():
    from features import Features

    router = make_search_router(Features(search=True))
    route = next(r for r in router.routes if r.path.endswith("/searches"))
    assert any(d.call is search_slot for d in route.dependant.dependencies)


def test_create_app_builds_the_limiter_from_settings(monkeypatch):
    monkeypatch.setenv("SEARCH_MAX_CONCURRENT", "3")
    monkeypatch.setenv("SEARCH_QUEUE_SIZE", "7")
    monkeypatch.setenv("SEARCH_QUEUE_TIMEOUT_SECONDS", "1.5")
    app = create_app(static_dir="", settings=Settings())
    limiter = app.state.search_limiter
    assert (limiter._slots._value, limiter._max_waiting, limiter.timeout) == (3, 7, 1.5)
