import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from database import dispose_engines
from features import Features, load_features
from limiter import SearchLimiter
from rest import API_PREFIX, EXPOSED_HEADERS, install_error_handlers
from settings import Settings, get_settings
from startup import check_embeddings_release, init_database, preload_resources

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")


def _docs_kwargs(settings: Settings) -> dict:
    """Prod serves no interactive docs and no OpenAPI schema."""
    if settings.is_prod:
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {}


def cors_settings(raw: str) -> dict:
    """CORS middleware kwargs from a comma-separated origin list.

    Auth is a bearer header, not a cookie, so browsers never need to send credentials.
    """
    if raw.strip() == "*":
        return {"allow_origins": ["*"], "allow_credentials": False}
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return {"allow_origins": origins, "allow_credentials": False}


# The SPA loads its own scripts and the Google Fonts stylesheet/fonts, nothing else.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' "
    "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; "
    "frame-ancestors 'none'; form-action 'self'"
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
# Swagger UI loads scripts from a CDN, so the strict policy would blank it.
DOCS_PATHS = ("/docs", "/redoc")


def add_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if not request.url.path.startswith(DOCS_PATHS):
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response


def resolve_static_dir(env_value: str | None = None) -> str | None:
    if env_value and os.path.isdir(env_value):
        return env_value
    candidates = [
        os.path.join(BASE_DIR, "..", "static"),
        os.path.join(BASE_DIR, "..", "frontend", "dist"),
    ]
    return next((os.path.abspath(c) for c in candidates if os.path.isdir(c)), None)


def _include_feature_routers(app: FastAPI, features: Features) -> None:
    from routers import (
        annotation_router,
        auth_router,
        benchmark_router,
        hadiths_router,
        kv_pairs_router,
        make_root_router,
        make_search_router,
        suggestions_router,
    )

    app.include_router(make_root_router(features))
    app.include_router(hadiths_router)
    if features.annotation:
        app.include_router(annotation_router)
        app.include_router(auth_router)
    if features.kv_pairs:
        app.include_router(kv_pairs_router)
    if features.search:
        app.include_router(make_search_router(features))
        app.include_router(suggestions_router)
    if features.benchmark:
        app.include_router(benchmark_router)


def _mount_frontend(app: FastAPI, static_dir: str) -> None:
    assets_dir = os.path.join(static_dir, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if f"/{full_path}".startswith(API_PREFIX):
            raise HTTPException(status_code=404, detail="No such API resource")
        # Resolve symlinks and ".." first: a decoded "../" must not leave the static folder.
        root = os.path.realpath(static_dir)
        candidate = os.path.realpath(os.path.join(root, full_path))
        if full_path and candidate.startswith(root + os.sep) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(static_dir, "index.html"))


def create_app(
    features: Features | None = None,
    static_dir: str | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Build the app; dependencies (feature flags, static dir, settings) are injected, env is the default."""
    features = features or load_features()
    settings = settings or get_settings()
    static_dir = static_dir or resolve_static_dir(settings.static_dir)
    os.makedirs(DATA_DIR, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            try:
                await init_database()
            except Exception as exc:
                raise RuntimeError(
                    f"Cannot reach the database ({type(exc).__name__}); "
                    "check DATABASE_URL and that PostgreSQL is running."
                ) from exc
            await check_embeddings_release()
            preload_resources(features)
            print(
                f"Serving frontend from: {static_dir}"
                if static_dir
                else "No frontend build found (STATIC_DIR not configured)"
            )
            yield
        finally:
            print("Shutting down...")
            await dispose_engines()

    app = FastAPI(lifespan=lifespan, **_docs_kwargs(settings))
    app.state.features = features
    app.state.search_limiter = SearchLimiter(
        settings.search_max_concurrent,
        settings.search_queue_size,
        settings.search_queue_timeout_seconds,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "If-None-Match"],
        expose_headers=EXPOSED_HEADERS,
        **cors_settings(settings.effective_cors_origins),
    )
    add_security_headers(app)
    install_error_handlers(app)
    _include_feature_routers(app, features)

    if static_dir:
        _mount_frontend(app, static_dir)
    return app


app = create_app()
