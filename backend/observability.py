"""Metrics for Prometheus (`GET /metrics`) and JSON logs that carry a request id.

Each app gets its own `CollectorRegistry`, so tests can build many apps in one process. `/metrics`
is internal: nginx answers 404 for it (nginx/default.conf) and Prometheus scrapes each colour
directly on the compose network (monitoring/prometheus/prometheus.yml). Alert rules over these
metrics are in monitoring/prometheus/alerts.yml (docs/HANDOFF.md item 47).

Logs never contain the query string, so search text stays out of them.
"""

import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    PlatformCollector,
    ProcessCollector,
)
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_fastapi_instrumentator import metrics as http_metrics

if TYPE_CHECKING:
    from limiter import SearchLimiter

# Seconds. Dense enough around the 1 s (warning) and 2 s (critical) search p95 thresholds.
LATENCY_BUCKETS = (0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10, 30)
NOT_MEASURED = ("/metrics", "/api/v1/health")  # scraped every 15 s; would drown the request rate
REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_HANDLER_MARK = "_hadith_json_handler"
_LOG_FIELDS = ("method", "path", "status", "duration_ms", "client")

logger = logging.getLogger("hadith.access")


def _stamp_request_id(make_record):
    """Record the request id when a log record is made, not when (or where) it is written."""

    def make(*args, **kwargs):
        record = make_record(*args, **kwargs)
        record.request_id = _request_id.get()
        return record

    make.stamps_request_id = True
    return make


if not getattr(logging.getLogRecordFactory(), "stamps_request_id", False):
    logging.setLogRecordFactory(_stamp_request_id(logging.getLogRecordFactory()))


@dataclass(frozen=True)
class AppMetrics:
    registry: CollectorRegistry
    search_seconds: Histogram
    search_db_seconds: Histogram
    search_encode_seconds: Histogram
    search_rejected: Counter


def build_metrics(limiter: "SearchLimiter | None" = None) -> AppMetrics:
    registry = CollectorRegistry()
    ProcessCollector(registry=registry)
    PlatformCollector(registry=registry)
    labels = ["method"]
    found = AppMetrics(
        registry=registry,
        search_seconds=Histogram(
            "search_duration_seconds",
            "Time spent ranking one search (no queue wait)",
            labels,
            buckets=LATENCY_BUCKETS,
            registry=registry,
        ),
        search_db_seconds=Histogram(
            "search_db_seconds",
            "Database time of one search",
            labels,
            buckets=LATENCY_BUCKETS,
            registry=registry,
        ),
        search_encode_seconds=Histogram(
            "search_encode_seconds",
            "Query encoder time of one search",
            labels,
            buckets=LATENCY_BUCKETS,
            registry=registry,
        ),
        search_rejected=Counter(
            "search_rejected",
            "Searches answered 503 by the queue",
            ["reason"],
            registry=registry,
        ),
    )
    if limiter is not None:
        _queue_gauges(registry, limiter)
    return found


def _queue_gauges(registry: CollectorRegistry, limiter: "SearchLimiter") -> None:
    waiting = Gauge("search_queue_waiting", "Searches waiting for a slot", registry=registry)
    waiting.set_function(lambda: limiter.waiting)
    running = Gauge("search_queue_running", "Searches holding a slot", registry=registry)
    running.set_function(lambda: limiter.running)


def install_metrics(app: FastAPI, found: AppMetrics) -> None:
    """Count and time every templated route, and serve the registry on `/metrics`."""
    app.state.metrics = found
    instrumentator = Instrumentator(
        should_group_status_codes=False,  # 503 (busy) and other 5xx are alerted on separately
        should_ignore_untemplated=True,
        excluded_handlers=list(NOT_MEASURED),
        registry=found.registry,
    )
    instrumentator.add(http_metrics.requests(registry=found.registry))
    instrumentator.add(
        http_metrics.latency(
            should_include_status=False,
            buckets=LATENCY_BUCKETS,
            registry=found.registry,
        )
    )
    instrumentator.instrument(app).expose(app, include_in_schema=False)


def record_search(request: Request, method: str, seconds: float, timings) -> None:
    found: AppMetrics | None = getattr(request.app.state, "metrics", None)
    if found is None:
        return
    found.search_seconds.labels(method).observe(seconds)
    found.search_db_seconds.labels(method).observe(timings.db_ms / 1000)
    if timings.encode_ms:
        found.search_encode_seconds.labels(method).observe(timings.encode_ms / 1000)


def record_rejected(request: Request, reason: str) -> None:
    found: AppMetrics | None = getattr(request.app.state, "metrics", None)
    if found is not None:
        found.search_rejected.labels(reason).inc()


def request_id_from(header: str | None) -> str:
    """nginx sends its `$request_id`; anything missing or odd gets a fresh id."""
    if header and _REQUEST_ID.match(header):
        return header
    return uuid.uuid4().hex


def add_request_logging(app: FastAPI) -> None:
    """One JSON access line per request, with the id echoed back in `X-Request-ID`."""

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        token = _request_id.set(request_id_from(request.headers.get(REQUEST_ID_HEADER)))
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[REQUEST_ID_HEADER] = _request_id.get()
            return response
        finally:
            _log_access(request, status, (time.perf_counter() - started) * 1000)
            _request_id.reset(token)


def _log_access(request: Request, status: int, duration_ms: float) -> None:
    path = request.url.path
    level = logging.DEBUG if path in NOT_MEASURED else logging.INFO
    client = request.headers.get("X-Real-IP") or (request.client.host if request.client else "")
    logger.log(
        level,
        "request",
        extra={
            "method": request.method,
            "path": path,
            "status": status,
            "duration_ms": round(duration_ms, 1),
            "client": client,
        },
    )


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        entry.update({k: getattr(record, k) for k in _LOG_FIELDS if hasattr(record, k)})
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def _json_handler() -> logging.Handler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARK, True)
    return handler


def configure_logging(log_format: str, level: str = "INFO") -> None:
    """`json`: one JSON object per line on stdout, for Loki. `text`: Python's default.

    Safe to call more than once; it replaces only the handler it added itself. uvicorn's own text
    handlers are dropped so its records reach the JSON handler on the root logger.
    """
    if log_format != "json":
        return
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not getattr(h, _HANDLER_MARK, False)]
    root.addHandler(_json_handler())
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        server_logger = logging.getLogger(name)
        server_logger.handlers = []
        server_logger.propagate = True
