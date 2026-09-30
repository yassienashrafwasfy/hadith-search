"""Request rate limiting (SlowAPI).

Every route gets a per-address default limit. Sign-in is counted per username (10 tries per
20 minutes for each account, wherever the tries come from) plus a looser per-address cap so
one address can't spray many usernames. Sign-up has no username to count yet, so it is
counted per address. Counters live in memory by default, which is right for
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
AUTH_LIMIT = "10/20 minutes"
AUTH_IP_LIMIT = "50/20 minutes"


def default_limit() -> str:
    return os.environ.get("RATE_LIMIT_DEFAULT", DEFAULT_LIMIT)


def auth_limit() -> str:
    return os.environ.get("RATE_LIMIT_AUTH", AUTH_LIMIT)


def auth_ip_limit() -> str:
    return os.environ.get("RATE_LIMIT_AUTH_IP", AUTH_IP_LIMIT)


def username_key(request: Request) -> str:
    """Counter key for sign-in: the account being tried (set by a dependency), else the address."""
    return (
        f"user:{getattr(request.state, 'rate_limit_username', None) or get_remote_address(request)}"
    )


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
