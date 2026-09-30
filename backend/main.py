import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from features import Features, load_features
from startup import init_database, preload_resources

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://192.168.1.6:5173,http://192.168.1.5:5173"


def cors_settings(raw: str) -> dict:
    """CORS middleware kwargs from a comma-separated origin list ('*' disables credentials)."""
    if raw.strip() == "*":
        return {"allow_origins": ["*"], "allow_credentials": False}
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return {"allow_origins": origins, "allow_credentials": True}


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
        kv_pairs_router,
        make_search_router,
    )

    if features.annotation:
        app.include_router(annotation_router)
        app.include_router(auth_router)
    if features.kv_pairs:
        app.include_router(kv_pairs_router)
    if features.search:
        app.include_router(make_search_router(features))
    if features.benchmark:
        app.include_router(benchmark_router)


def _mount_frontend(app: FastAPI, static_dir: str) -> None:
    assets_dir = os.path.join(static_dir, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        candidate = os.path.join(static_dir, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(static_dir, "index.html"))


def create_app(features: Features | None = None, static_dir: str | None = None) -> FastAPI:
    """Build the app; dependencies (feature flags, static dir) are injected, env is the default."""
    features = features or load_features()
    static_dir = static_dir or resolve_static_dir(os.environ.get("STATIC_DIR"))
    os.makedirs(DATA_DIR, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_database()
        preload_resources(features)
        print(
            f"Serving frontend from: {static_dir}"
            if static_dir
            else "No frontend build found (STATIC_DIR not configured)"
        )
        yield
        print("Shutting down...")

    app = FastAPI(lifespan=lifespan)
    app.state.features = features
    app.add_middleware(
        CORSMiddleware,
        allow_methods=["*"],
        allow_headers=["*"],
        **cors_settings(os.environ.get("CORS_ORIGINS", DEFAULT_CORS_ORIGINS)),
    )
    _include_feature_routers(app, features)

    @app.get("/hadith/{hadith_id}")
    def get_hadith(hadith_id: int):
        from database import get_hadith_row

        row = get_hadith_row(hadith_id)
        return {"error": "not found"} if row is None else row

    if static_dir:
        _mount_frontend(app, static_dir)
    return app


app = create_app()
