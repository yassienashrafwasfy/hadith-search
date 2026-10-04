import asyncio
import types

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pytest_bdd import given, scenarios, then, when

from features import Features
from limiter import SearchLimiter
from main import create_app
from rest import install_error_handlers
from routers.search import get_search_context, make_search_router
from services.retrieval import SearchContext
from settings import Settings

scenarios("../../docs/behaviours/spike-queue.feature")

SEARCH = ("/api/v1/searches", {"q": "prayer", "method": "bm25"})


async def _wait_until(condition, tries=400):
    for _ in range(tries):
        if condition():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached")


# ---------- the limiter alone ----------


@given("the app allows N searches at once and a queue of M")
def _limiter_two_and_three(_ctx):
    _ctx["limiter"], _ctx["order"], _ctx["release"] = SearchLimiter(2, 3, 5), [], asyncio.Event()


@when("more than N searches arrive")
def _five_arrive(_ctx, _run):
    limiter, order, release = _ctx["limiter"], _ctx["order"], _ctx["release"]

    async def job(n):
        async with limiter.slot():
            order.append(n)
            await release.wait()

    async def arrive():
        _ctx["tasks"] = [asyncio.create_task(job(n)) for n in range(5)]
        await _wait_until(lambda: len(order) == 2 and limiter.waiting == 3)

    _run(arrive())


@then("N run and the others wait first come, first served")
def _run_then_queue(_ctx, _run):
    assert _ctx["order"] == [0, 1] and _ctx["limiter"].waiting == 3
    _ctx["release"].set()
    _run(asyncio.gather(*_ctx["tasks"]))
    assert _ctx["order"] == [0, 1, 2, 3, 4] and _ctx["limiter"].waiting == 0


@given("a search holds a slot")
def _limiter_one(_ctx):
    _ctx["limiter"] = SearchLimiter(1, 0, 1)


@when("its handler raises")
def _handler_raises(_ctx, _run):
    async def failing():
        with pytest.raises(ValueError):
            async with _ctx["limiter"].slot():
                raise ValueError

    _run(failing())


@then("the slot is released for the next request")
def _slot_released(_ctx, _run):
    async def next_request():
        async with asyncio.timeout(1):
            async with _ctx["limiter"].slot():
                pass

    _run(next_request())


# ---------- the real search route behind a limiter ----------


@pytest.fixture
def _spike(_search_index, _fake_model, _mock_preprocess, _run):
    """The real search router whose database session waits for a gate, so a request keeps its slot."""
    import database

    gate = asyncio.Event()

    async def context():
        await gate.wait()
        with database.get_sync_session() as session:
            yield SearchContext(session=session, model=lambda: _fake_model)

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(make_search_router(Features(search=True, dense_retrieval=False)))
    app.dependency_overrides[get_search_context] = context
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    spike = types.SimpleNamespace(app=app, client=client, gate=gate, tasks=[])
    yield spike

    async def finish():
        gate.set()
        await asyncio.gather(*spike.tasks, return_exceptions=True)
        await client.aclose()

    _run(finish())


def _start_search(spike, run):
    async def start():
        spike.tasks.append(asyncio.create_task(spike.client.get(SEARCH[0], params=SEARCH[1])))

    run(start())


def _search_now(spike, ctx, run):
    ctx["responses"] = [run(spike.client.get(SEARCH[0], params=SEARCH[1]))]


def _use_limiter(spike, run, running, waiting, timeout):
    spike.limiter = spike.app.state.search_limiter = SearchLimiter(running, waiting, timeout)
    _start_search(spike, run)
    run(_wait_until(lambda: spike.limiter._slots.locked()))


@given("every slot is busy and the queue is full")
def _everything_busy(_spike, _run):
    _use_limiter(_spike, _run, 1, 0, 2.2)


@when("another search arrives")
def _another_search(_spike, _ctx, _run):
    _search_now(_spike, _ctx, _run)


@then("the answer is 503 with a Retry-After header")
def _busy_answer(_ctx):
    response = _ctx["responses"][0]
    assert response.status_code == 503 and "retry-after" in response.headers


@then("the body status is 503")
def _body_status(_ctx):
    assert _ctx["responses"][0].json()["status"] == 503


@then('the Retry-After value is the queue timeout rounded up (a 2.2 s timeout gives "3")')
def _retry_after_rounded(_ctx):
    assert _ctx["responses"][0].headers["retry-after"] == "3"


@given("a search is waiting for a slot")
def _one_waiting(_spike, _run):
    _use_limiter(_spike, _run, 1, 4, 0.05)
    _start_search(_spike, _run)
    _run(_wait_until(lambda: _spike.limiter.waiting == 1))


@when("it waits longer than the timeout")
def _waits_too_long(_spike, _ctx, _run):
    _ctx["responses"] = [_run(_spike.tasks[1])]


@then("it gets the same 503 and its place in the queue is freed")
def _gives_up(_spike, _ctx):
    response = _ctx["responses"][0]
    assert response.status_code == 503 and response.headers["retry-after"] == "1"
    assert response.headers["content-type"].startswith("application/problem+json")
    assert _spike.limiter.waiting == 0


@given("the queue was full and the running search finishes")
def _queue_full_then_done(_spike, _ctx, _run):
    _use_limiter(_spike, _run, 1, 0, 2.2)
    _search_now(_spike, _ctx, _run)
    assert _ctx["responses"][0].status_code == 503
    _spike.gate.set()
    assert _run(_spike.tasks[0]).status_code == 200


@when("a new search arrives")
def _new_search(_spike, _ctx, _run):
    _search_now(_spike, _ctx, _run)


@then("it is answered with 200")
def _answered(_ctx):
    assert _ctx["responses"][0].status_code == 200


# ---------- without a limiter, and built from settings ----------


@given("the app has no search_limiter in its state")
def _no_limiter(_search_client):
    assert not hasattr(_search_client._transport.app.state, "search_limiter")


@then("searches are never queued or refused")
def _never_refused(_search_client, _run):
    async def burst():
        return await asyncio.gather(
            *[_search_client.get(*SEARCH[:1], params=SEARCH[1]) for _ in range(20)]
        )

    assert {r.status_code for r in _run(burst())} == {200}


@given("SEARCH_MAX_CONCURRENT, SEARCH_QUEUE_SIZE and SEARCH_QUEUE_TIMEOUT_SECONDS are set")
def _limits_in_env(monkeypatch):
    monkeypatch.setenv("SEARCH_MAX_CONCURRENT", "3")
    monkeypatch.setenv("SEARCH_QUEUE_SIZE", "7")
    monkeypatch.setenv("SEARCH_QUEUE_TIMEOUT_SECONDS", "1.5")


@when("the app is created")
def _create_app(_ctx):
    _ctx["app"] = create_app(static_dir="", settings=Settings())


@then("app.state.search_limiter uses those values")
def _limiter_from_settings(_ctx):
    limiter = _ctx["app"].state.search_limiter
    assert (limiter._slots._value, limiter._max_waiting, limiter.timeout) == (3, 7, 1.5)
