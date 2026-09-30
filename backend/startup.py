"""Startup work: create tables and (optionally) warm the retrieval resources."""

from collections.abc import Callable

from features import Features


def _run_step(label: str, load: Callable[[], object]) -> None:
    print(f"  - {label}... ", end="", flush=True)
    load()
    print("done")


def _sparse_steps() -> list[tuple[str, Callable[[], object]]]:
    from scripts import (
        get_arabic_inverted_index,
        get_document_lengths,
        get_english_inverted_index,
        get_hadith_ids,
        get_hadiths_df,
    )

    return [
        ("English inverted index", get_english_inverted_index),
        ("Arabic inverted index", get_arabic_inverted_index),
        ("Document lengths", get_document_lengths),
        ("Hadith IDs", get_hadith_ids),
        ("Hadiths DataFrame", get_hadiths_df),
    ]


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
    print("Loading indices and models at startup...")
    for label, load in _sparse_steps() + _model_steps(features):
        _run_step(label, load)
    if features.dense_retrieval and not features.eager_model:
        print("Dense retrieval enabled: lazy-loading E5 model on first request")


async def init_database() -> None:
    from database import init_schema

    await init_schema()
