"""Request rate limiting (SlowAPI).

Every route gets a per-client default limit; sign-in and sign-up get a much lower one so a
password can't be guessed at speed. Counters live in memory by default, which is right for
one server. With several servers each would count on its own, so point them at a shared
store with `RATE_LIMIT_STORAGE_URI` (e.g. `redis://cache:6379`; needs the `redis` package).

Behind a proxy the client address is the proxy's unless uvicorn trusts its forwarded
headers: set `FORWARDED_ALLOW_IPS` to the proxy's address.
"""

import os

from fastapi import FastAPI, Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from rest import problem_response

DEFAULT_LIMIT = "120/minute"
AUTH_LIMIT = "10/minute"


def default_limit() -> str:
    return os.environ.get("RATE_LIMIT_DEFAULT", DEFAULT_LIMIT)


def auth_limit() -> str:
    return os.environ.get("RATE_LIMIT_AUTH", AUTH_LIMIT)


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[default_limit],
    storage_uri=os.environ.get("RATE_LIMIT_STORAGE_URI", "memory://"),
    headers_enabled=True,
    enabled=os.environ.get("RATE_LIMIT_ENABLED", "true").lower() not in ("0", "false", "no"),
)


async def _too_many_requests(_request: Request, exc: RateLimitExceeded):
    window_seconds = exc.limit.limit.get_expiry()
    return problem_response(
        429,
        f"Rate limit exceeded ({exc.detail}). Try again in {window_seconds} seconds.",
        headers={"Retry-After": str(window_seconds)},
    )


def install_rate_limiting(app: FastAPI) -> None:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _too_many_requests)
    app.add_middleware(SlowAPIMiddleware)
