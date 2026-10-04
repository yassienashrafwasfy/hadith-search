"""Signed bearer tokens (JWT, HS256).

Nothing is stored server-side: any server that knows `AUTH_SECRET` can verify a token, which
is what lets several instances sit behind one load balancer. The cost is that a token cannot
be revoked before it expires (there is no sign-out call; the client just drops it).
"""

import logging
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache

import jwt

import settings as settings_module

logger = logging.getLogger(__name__)

DEFAULT_TTL_MINUTES = settings_module.DEFAULT_TTL_MINUTES


@dataclass(frozen=True)
class AuthSettings:
    secret: str
    ttl_seconds: int


@lru_cache
def auth_settings() -> AuthSettings:
    """Built once from `get_settings()`; override this dependency in tests."""
    env = settings_module.get_settings()
    secret = env.auth_secret.get_secret_value() if env.auth_secret else None
    if not secret:
        secret = secrets.token_urlsafe(32)
        logger.warning(
            "AUTH_SECRET is not set: using a random secret, so tokens stop working on "
            "restart and are rejected by any other server. Set AUTH_SECRET to share it."
        )
    return AuthSettings(secret=secret, ttl_seconds=env.auth_token_ttl_minutes * 60)


def issue_token(annotator_id: int, username: str, settings: AuthSettings) -> str:
    now = int(time.time())
    claims = {
        "sub": str(annotator_id),
        "name": username,
        "iat": now,
        "exp": now + settings.ttl_seconds,
    }
    return jwt.encode(claims, settings.secret, algorithm="HS256")


def read_token(token: str, settings: AuthSettings) -> dict | None:
    """The annotator `{"id", "username"}` a valid token names, or None if invalid/expired."""
    try:
        claims = jwt.decode(
            token, settings.secret, algorithms=["HS256"], options={"require": ["exp", "sub"]}
        )
        return {"id": int(claims["sub"]), "username": claims["name"]}
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None
