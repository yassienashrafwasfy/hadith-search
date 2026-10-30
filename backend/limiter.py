"""A bounded queue in front of the expensive search route, for traffic spikes.

At most `running` searches run at once. When all slots are busy the next `waiting` requests wait
their turn (first come, first served) for at most `timeout` seconds; anything beyond that, or
anything that waits too long, is answered at once with 503 and a Retry-After header. Waiting is
cheap (an idle coroutine); running is not (ONNX encode and SQL ranking use the CPU), so this keeps
a spike from turning into one long pile of slow requests.

nginx smooths each client address on its own (nginx/default.conf); this is the one limit shared by
every client of this process. With two colours or workers each has its own queue.
"""

import asyncio
import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException, Request

from observability import record_rejected

logger = logging.getLogger(__name__)


class Overloaded(Exception):
    """The queue is full or the wait ran out. `reason` is the metric label."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


class SearchLimiter:
    def __init__(self, running: int, waiting: int, timeout: float):
        self.timeout = timeout
        self._slots = asyncio.Semaphore(running)
        self._max_running = running
        self._max_waiting = waiting
        self._waiting = 0

    @property
    def waiting(self) -> int:
        return self._waiting

    @property
    def running(self) -> int:
        return self._max_running - self._slots._value

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        if self._slots.locked():
            await self._wait_for_slot()
        else:
            await self._slots.acquire()
        try:
            yield
        finally:
            self._slots.release()

    async def _wait_for_slot(self) -> None:
        if self._waiting >= self._max_waiting:
            raise Overloaded("queue full", "queue_full")
        self._waiting += 1
        try:
            async with asyncio.timeout(self.timeout):
                await self._slots.acquire()
        except TimeoutError:
            raise Overloaded("waited too long", "timeout") from None
        finally:
            self._waiting -= 1


async def search_slot(request: Request) -> AsyncIterator[None]:
    """FastAPI dependency: hold a search slot for the whole request, or answer 503.

    An app without `app.state.search_limiter` (most tests) is not limited.
    """
    limiter: SearchLimiter | None = getattr(request.app.state, "search_limiter", None)
    if limiter is None:
        yield
        return
    try:
        async with limiter.slot():
            yield
    except Overloaded as exc:
        logger.warning("search rejected: %s (%d waiting)", exc, limiter.waiting)
        record_rejected(request, exc.reason)
        raise HTTPException(
            status_code=503,
            detail="The server is busy. Try again in a moment.",
            headers={"Retry-After": str(math.ceil(limiter.timeout))},
        ) from None
