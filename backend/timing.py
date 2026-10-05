"""Per-request timing of database statements and the query encoder, for the Server-Timing header.

`start()` opens a record for the current request; SQLAlchemy's cursor events and `time_encode`
add to it. Outside a request nothing is recorded.
"""

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import event
from sqlalchemy.engine import Engine


@dataclass
class Timings:
    db_ms: float = 0.0
    statements: int = 0
    encode_ms: float = 0.0


_current: ContextVar[Timings | None] = ContextVar("request_timings", default=None)


def start() -> Timings:
    timings = Timings()
    _current.set(timings)
    return timings


@contextmanager
def time_encode():
    started = time.perf_counter()
    try:
        yield
    finally:
        timings = _current.get()
        if timings is not None:
            timings.encode_ms += (time.perf_counter() - started) * 1000


def metrics(timings: Timings) -> list[str]:
    """Server-Timing entries (after `search`) for what this request spent."""
    entries = [
        f'db;dur={timings.db_ms:.1f};desc="{timings.statements} statements"',
    ]
    if timings.encode_ms:
        entries.append(f"encode;dur={timings.encode_ms:.1f}")
    return entries


@event.listens_for(Engine, "before_cursor_execute")
def _statement_started(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("statement_started", []).append(time.perf_counter())


@event.listens_for(Engine, "after_cursor_execute")
def _statement_finished(conn, cursor, statement, parameters, context, executemany):
    started = conn.info["statement_started"].pop()
    timings = _current.get()
    if timings is not None:
        timings.db_ms += (time.perf_counter() - started) * 1000
        timings.statements += 1
