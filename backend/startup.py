"""Startup work: create tables and (optionally) warm the retrieval resources."""

from collections.abc import Callable

from features import Features


def _run_step(label: str, load: Callable[[], object]) -> None:
    print(f"  - {label}... ", end="", flush=True)
    load()
    print("done")


def check_index() -> None:
    """Warn when the search index tables are empty (run the build scripts first)."""
    from sqlalchemy import func, select

    from database import get_sync_session
    from models import Posting

    with get_sync_session() as session:
        if session.scalar(select(func.count()).select_from(Posting).limit(1)) == 0:
            print("WARNING: no postings in the database; searches will be empty. ", end="")


def _index_steps() -> list[tuple[str, Callable[[], object]]]:
    return [("Search index in PostgreSQL", check_index)]


def _model_steps(features: Features) -> list[tuple[str, Callable[[], object]]]:
    if not (features.dense_retrieval and features.eager_model):
        return []
    from scripts import get_model

    return [("Sentence Transformer model (intfloat/multilingual-e5-large)", get_model)]


def preload_resources(features: Features) -> None:
    """Load what the enabled features need; the E5 model stays lazy unless `eager_model`."""
    if not features.search:
        print("Search disabled: skipping model/index preload")
        return
    print("Checking the search index and loading models at startup...")
    for label, load in _index_steps() + _model_steps(features):
        _run_step(label, load)
    if features.dense_retrieval and not features.eager_model:
        print("Dense retrieval enabled: lazy-loading E5 model on first request")


async def init_database() -> None:
    from database import init_schema

    await init_schema()
