"""Environment settings, read once.

`get_settings()` builds `Settings` from the process environment (and the repo-root `.env`, which
the real environment overrides) the first time it is called and returns the same object after
that. Tests that change the environment call `get_settings.cache_clear()`.

`APP_ENV` is the state of the deployment:

- `dev` (default) and `test`: forgiving. A missing `AUTH_SECRET` gets a random one, and CORS
  falls back to the local Vite origins.
- `prod`: refuses to start without `AUTH_SECRET` and `CORS_ORIGINS` (and `CORS_ORIGINS=*`), and
  hides the interactive API docs and `/openapi.json`.

Feature flags (`APP_MODE`, `FEATURE_*`) are not here; see `features.py`.
"""

import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_TTL_MINUTES = 720
MIN_SECRET_LENGTH = 32  # HS256 wants a key as long as its 256-bit hash
DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
DEFAULT_SEARCH_RUNNING = 8
DEFAULT_SEARCH_WAITING = 64
DEFAULT_SEARCH_TIMEOUT_SECONDS = 5.0
DEFAULT_DB_POOL_OVERFLOW = 2
DEFAULT_DB_POOL_RECYCLE_SECONDS = 1800
DEFAULT_ENCODER_BATCH_WAIT_MS = 0.0
DEFAULT_ENCODER_BATCH_MAX = 16
DEFAULT_ENCODER_THREADS = 1  # a query is one short text; see handoff item 24 for why not more
RELEASE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def check_release(release: str) -> str:
    """A model release name is part of a table name, so it is restricted."""
    if not RELEASE_PATTERN.match(release):
        raise ValueError(
            f"Embeddings release {release!r} must start with a letter and use only lowercase "
            "letters, digits and _ (40 characters at most)"
        )
    return release


class Environment(StrEnum):
    DEV = "dev"
    PROD = "prod"
    TEST = "test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore", frozen=True)

    app_env: Environment = Environment.DEV
    database_url: str | None = Field(default=None, repr=False)
    auth_secret: SecretStr | None = None
    auth_token_ttl_minutes: int = Field(default=DEFAULT_TTL_MINUTES, gt=0)
    cors_origins: str | None = None
    static_dir: str | None = None
    arabic_model_dir: str | None = None
    arabic_encoder_threads: int = Field(default=DEFAULT_ENCODER_THREADS, ge=0)
    # Which `hadith_embeddings_<release>` table dense search reads (unset: `hadith_embeddings`)
    embeddings_release: str | None = None
    # The queue in front of /api/v1/searches (backend/limiter.py)
    search_max_concurrent: int = Field(default=DEFAULT_SEARCH_RUNNING, gt=0)
    search_queue_size: int = Field(default=DEFAULT_SEARCH_WAITING, ge=0)
    search_queue_timeout_seconds: float = Field(default=DEFAULT_SEARCH_TIMEOUT_SECONDS, gt=0)
    # Connection pool per engine: one connection per running search (None: SEARCH_MAX_CONCURRENT)
    db_pool_size: int | None = Field(default=None, gt=0)
    db_pool_overflow: int = Field(default=DEFAULT_DB_POOL_OVERFLOW, ge=0)
    db_pool_recycle_seconds: int = Field(default=DEFAULT_DB_POOL_RECYCLE_SECONDS, gt=0)
    # Queries that arrive within this many ms share one encoder call. 0 (default) is off: on 2 CPUs
    # it gave no throughput gain and added the wait to every query (docs/HANDOFF.md item 42)
    encoder_batch_wait_ms: float = Field(default=DEFAULT_ENCODER_BATCH_WAIT_MS, ge=0)
    encoder_batch_max: int = Field(default=DEFAULT_ENCODER_BATCH_MAX, gt=0)

    @field_validator("embeddings_release")
    @classmethod
    def _release_name(_cls, value: str | None) -> str | None:
        if not value:
            return None  # compose passes an empty string when no release is set
        return check_release(value)

    @property
    def is_prod(self) -> bool:
        return self.app_env is Environment.PROD

    @property
    def effective_cors_origins(self) -> str:
        return self.cors_origins or DEFAULT_CORS_ORIGINS

    @model_validator(mode="after")
    def _check_secret_and_prod_rules(self) -> "Settings":
        secret = self.auth_secret.get_secret_value() if self.auth_secret else None
        if secret is not None and len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(f"AUTH_SECRET must be at least {MIN_SECRET_LENGTH} characters long")
        if self.is_prod:
            missing = [
                name
                for name, value in (("AUTH_SECRET", secret), ("CORS_ORIGINS", self.cors_origins))
                if not value
            ]
            if missing:
                raise ValueError(f"APP_ENV=prod requires {', '.join(missing)} to be set")
            if "*" in [origin.strip() for origin in (self.cors_origins or "").split(",")]:
                raise ValueError(
                    "APP_ENV=prod does not allow CORS_ORIGINS=*; list the real origins"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
