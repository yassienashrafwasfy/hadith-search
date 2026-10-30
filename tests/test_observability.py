import asyncio
import json
import logging

from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from features import Features
from limiter import SearchLimiter, search_slot
from main import create_app
from observability import (
    JsonFormatter,
    add_request_logging,
    build_metrics,
    configure_logging,
    install_metrics,
    request_id_from,
)
from rest import install_error_handlers


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _sample(text: str, name: str, **labels) -> float | None:
    """Value of one sample in Prometheus text output, or None when it is missing."""
    want = ",".join(f'{k}="{v}"' for k, v in labels.items())
    for line in text.splitlines():
        if line.startswith(f"{name}{{{want}}}" if want else f"{name} "):
            return float(line.rsplit(" ", 1)[1])
    return None


async def test_metrics_count_requests_by_route_and_exact_status():
    app = create_app(features=Features(search=False), static_dir="")
    async with _client(app) as client:
        await client.get("/api/v1")
        await client.get("/api/v1/health")
        text = (await client.get("/metrics")).text
    labels = {"handler": "/api/v1", "method": "GET", "status": "200"}
    assert _sample(text, "http_requests_total", **labels) == 1
    assert 'handler="/api/v1/health"' not in text  # scraped often; would drown the rate
    assert 'handler="/metrics"' not in text
    assert _sample(text, "search_queue_waiting") == 0
    assert _sample(text, "search_queue_running") == 0


async def test_each_app_has_its_own_registry():
    first = create_app(features=Features(search=False), static_dir="")
    second = create_app(features=Features(search=False), static_dir="")
    assert first.state.metrics.registry is not second.state.metrics.registry


def test_metrics_can_be_switched_off():
    app = create_app(features=Features(search=False, metrics=False), static_dir="")
    assert not any(getattr(r, "path", "") == "/metrics" for r in app.routes)


async def test_a_search_records_its_ranking_time_per_method(_search_client):
    app = _search_client._transport.app
    install_metrics(app, build_metrics())
    res = await _search_client.get("/api/v1/searches", params={"q": "prayer", "method": "bm25"})
    assert res.status_code == 200
    text = (await _search_client.get("/metrics")).text
    assert _sample(text, "search_duration_seconds_count", method="bm25") == 1
    assert _sample(text, "search_db_seconds_count", method="bm25") == 1
    assert _sample(text, "search_encode_seconds_count", method="bm25") is None  # no encoder


async def test_a_full_queue_is_counted_by_reason():
    app = FastAPI()
    install_error_handlers(app)
    app.state.search_limiter = SearchLimiter(1, 0, 2.0)
    install_metrics(app, build_metrics(app.state.search_limiter))
    gate = asyncio.Event()

    @app.get("/work", dependencies=[Depends(search_slot)])
    async def work():
        await gate.wait()
        return {}

    async with _client(app) as client:
        running = asyncio.create_task(client.get("/work"))
        await asyncio.sleep(0.05)
        text = (await client.get("/metrics")).text
        assert _sample(text, "search_queue_running") == 1
        assert (await client.get("/work")).status_code == 503
        gate.set()
        await running
        text = (await client.get("/metrics")).text
    assert _sample(text, "search_rejected_total", reason="queue_full") == 1
    assert _sample(text, "http_requests_total", handler="/work", method="GET", status="503") == 1


def test_request_id_from_keeps_a_sane_id_and_replaces_anything_else():
    assert request_id_from("abc-123") == "abc-123"
    for bad in (None, "", "x" * 65, "a b", "id\nforged"):
        fresh = request_id_from(bad)
        assert fresh != bad and len(fresh) == 32


async def test_the_request_id_is_echoed_and_logged_without_the_query(caplog):
    app = FastAPI()
    add_request_logging(app)

    @app.get("/thing")
    async def thing():
        logging.getLogger("hadith.test").info("inside")
        return {}

    with caplog.at_level(logging.INFO):
        async with _client(app) as client:
            res = await client.get("/thing?q=secret", headers={"X-Request-ID": "req-1"})
    assert res.headers["x-request-id"] == "req-1"
    access = next(r for r in caplog.records if r.name == "hadith.access")
    assert (access.method, access.path, access.status) == ("GET", "/thing", 200)
    entries = [json.loads(JsonFormatter().format(r)) for r in caplog.records]
    assert {e["request_id"] for e in entries if e["msg"] in {"inside", "request"}} == {"req-1"}
    ours = [r for r in caplog.records if r.name.startswith("hadith")]
    assert not any("secret" in JsonFormatter().format(r) for r in ours)


def test_json_formatter_writes_one_object_with_the_extra_fields():
    record = logging.LogRecord("x", logging.WARNING, __file__, 1, "hi %s", ("there",), None)
    record.status = 503
    entry = json.loads(JsonFormatter().format(record))
    assert entry["msg"] == "hi there" and entry["level"] == "warning"
    assert entry["status"] == 503 and entry["request_id"] == "-"


def test_configure_logging_adds_one_json_handler_however_often_it_runs():
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging("text")
        assert root.handlers == before
        configure_logging("json")
        configure_logging("json")
        added = [h for h in root.handlers if h not in before]
        assert len(added) == 1 and isinstance(added[0].formatter, JsonFormatter)
        assert logging.getLogger("uvicorn.error").handlers == []
    finally:
        root.handlers = before
