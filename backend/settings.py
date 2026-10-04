"""Environment settings, read once.

`get_settings()` builds `Settings` from the process environment (and the repo-root `.env`, which
the real environment overrides) the first time it is called and returns the same object after
that. Tests that change the environment call `get_settings.cache_clear()`.

`APP_ENV` is the state of the deployment:

- `dev` (default) and `test`: forgiving. A missing `AUTH_SECRET` gets a random one, and CORS
  falls back to the local Vite origins.
- `prod`: refuses to start without `AUTH_SECRET` and `CORS_ORIGINS`, and hides the interactive
  API docs and `/openapi.json`.

Feature flags (`APP_MODE`, `FEATURE_*`) are not here; see `features.py`.
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_TTL_MINUTES = 720
MIN_SECRET_LENGTH = 32  # HS256 wants a key as long as its 256-bit hash
DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://192.168.1.6:5173,http://192.168.1.5:5173"
DEFAULT_ENCODER_THREADS = 1  # a query is one short text; see handoff item 24 for why not more
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


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
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
