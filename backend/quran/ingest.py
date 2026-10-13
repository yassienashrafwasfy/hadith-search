"""Ingestion chain of one source: tokenise -> chunk -> embed -> summarise -> write nodes.

Input: the ayah rows a loader inserted (level=ayah). Output: a surah node per surah, passage nodes
below it (`parent_id` = surah) and every ayah pointing at its passage. Node `text` always holds the
ORIGINAL text. A summary goes in `summary`; for passages and surahs the vector is the embedding of
that summary (routing and coarse retrieval), or of the text when no summary exists. Ayah vectors
are always of the cleaned `search_text`.
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session

from quran.chunking import semantic_passages
from quran.summarize import summarise_many
from quran.types import Encoder, Level, LLMClient

SEPARATOR = " "
EMBED_BATCH = 64


@dataclass(frozen=True)
class AyahNode:
    id: int
    surah: int
    ayah: int
    text: str
    search_text: str
    juz: int | None
    place: str | None
    author: str | None


@dataclass(frozen=True)
class BuildStats:
    ayat: int
    passages: int
    surahs: int
    summarised: int


def word_count(text: str) -> int:
    """Default token counter; scripts pass the Fada tokenizer's count instead."""
    return len(text.split())


def embed(encoder: Encoder, texts: Sequence[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        vectors.extend(
            np.asarray(encoder.encode(list(texts[start : start + EMBED_BATCH]))).tolist()
        )
    return vectors


def read_ayat(session: Session, table) -> list[AyahNode]:
    rows = session.execute(
        select(table)
        .where(table.c.level == Level.AYAH.value)
        .order_by(table.c.surah, table.c.ayah_from)
    ).all()
    return [
        AyahNode(r.id, r.surah, r.ayah_from, r.text, r.search_text, r.juz, r.place, r.author)
        for r in rows
    ]


def group_by_surah(ayat: Sequence[AyahNode]) -> dict[int, list[AyahNode]]:
    groups: dict[int, list[AyahNode]] = {}
    for item in ayat:
        groups.setdefault(item.surah, []).append(item)
    return groups


def clean_search_texts(
    session: Session, table, ayat: list[AyahNode], normalise: Callable[[str], str]
) -> list[AyahNode]:
    """Fill `search_text` (cleaned copy; `text` is never touched) where it is missing or stale."""
    fixed = []
    for item in ayat:
        wanted = normalise(item.text)
        if item.search_text != wanted:
            session.execute(update(table).where(table.c.id == item.id).values(search_text=wanted))
            item = AyahNode(**{**item.__dict__, "search_text": wanted})
        fixed.append(item)
    return fixed


def store_ayah_vectors(session: Session, table, ayat: list[AyahNode], vectors) -> None:
    for item, vector in zip(ayat, vectors, strict=True):
        session.execute(update(table).where(table.c.id == item.id).values(embedding=vector))


def clear_derived(session: Session, table) -> None:
    """Remove passage and surah nodes (ayat are detached first: the FK cascades on delete)."""
    session.execute(update(table).values(parent_id=None))
    session.execute(delete(table).where(table.c.level != Level.AYAH.value))


def _meta(group: Sequence[AyahNode]) -> dict:
    first = group[0]
    return {
        "surah": first.surah,
        "juz": first.juz,
        "place": first.place,
        "author": first.author,
    }


class _Writer:
    def __init__(self, session: Session, table):
        self.session, self.table = session, table

    def node(self, level, parent_id, group, text, search, summary, vector) -> int:
        values = {
            **_meta(group),
            "level": level.value,
            "parent_id": parent_id,
            "text": text,
            "search_text": search,
            "summary": summary,
            "embedding": vector,
            "ayah_from": group[0].ayah,
            "ayah_to": group[-1].ayah,
        }
        stmt = insert(self.table).values(**values).returning(self.table.c.id)
        return self.session.execute(stmt).scalar_one()

    def attach(self, ayat: Sequence[AyahNode], parent_id: int) -> None:
        ids = [a.id for a in ayat]
        self.session.execute(
            update(self.table).where(self.table.c.id.in_(ids)).values(parent_id=parent_id)
        )


def split_passages(
    group: Sequence[AyahNode],
    vectors: np.ndarray,
    count: Callable[[str], int],
    **chunk_options,
) -> list[list[AyahNode]]:
    tokens = [count(a.search_text) for a in group]
    return [
        [group[i] for i in span] for span in semantic_passages(tokens, vectors, **chunk_options)
    ]


def join(group: Sequence[AyahNode], field: str) -> str:
    return SEPARATOR.join(getattr(a, field) for a in group)


def summaries_for(
    llm: LLMClient | None, texts: list[str], level: str, concurrency: int
) -> list[str | None]:
    if llm is None or not texts:
        return [None] * len(texts)
    return asyncio.run(summarise_many(llm, texts, level, concurrency))


def surah_summary_input(passage_summaries: Sequence[str | None], fallback: str) -> str:
    kept = [s for s in passage_summaries if s]
    return SEPARATOR.join(kept) if kept else fallback


def write_surah(
    writer: _Writer,
    encoder: Encoder,
    llm: LLMClient | None,
    group: list[AyahNode],
    passages: list[list[AyahNode]],
    concurrency: int,
) -> tuple[int, int]:
    """Write one surah with its passages. Returns (passage count, summaries made)."""
    texts = [join(p, "text") for p in passages]
    searches = [join(p, "search_text") for p in passages]
    p_sums = summaries_for(llm, searches, "passage", concurrency)
    s_input = surah_summary_input(p_sums, join(group, "search_text"))
    (s_sum,) = summaries_for(llm, [s_input], "surah", concurrency)
    vectors = embed(encoder, [s or t for s, t in zip(p_sums, searches, strict=True)])
    (surah_vec,) = embed(encoder, [s_sum or join(group, "search_text")])
    surah_id = writer.node(
        Level.SURAH, None, group, join(group, "text"), join(group, "search_text"), s_sum, surah_vec
    )
    for span, text, search, summary, vector in zip(
        passages, texts, searches, p_sums, vectors, strict=True
    ):
        pid = writer.node(Level.PASSAGE, surah_id, span, text, search, summary, vector)
        writer.attach(span, pid)
    made = sum(1 for s in [*p_sums, s_sum] if s)
    return len(passages), made


def build_source(
    session: Session,
    table,
    encoder: Encoder,
    llm: LLMClient | None = None,
    *,
    normalise: Callable[[str], str] | None = None,
    count: Callable[[str], int] = word_count,
    concurrency: int = 1,
    **chunk_options,
) -> BuildStats:
    """Build passages, surahs, vectors and summaries for the ayat already in `table`.

    Safe to re-run: earlier passage and surah nodes are replaced. `llm=None` skips summaries.
    The caller commits.
    """
    if normalise is None:
        from scripts.arabic_encoder import encoding_text as normalise
    clear_derived(session, table)
    ayat = clean_search_texts(session, table, read_ayat(session, table), normalise)
    vectors = embed(encoder, [a.search_text for a in ayat])
    store_ayah_vectors(session, table, ayat, vectors)
    by_id = dict(zip((a.id for a in ayat), vectors, strict=True))
    writer = _Writer(session, table)
    passages = made = 0
    groups = group_by_surah(ayat)
    for group in groups.values():
        matrix = np.array([by_id[a.id] for a in group])
        spans = split_passages(group, matrix, count, **chunk_options)
        done, summaries = write_surah(writer, encoder, llm, group, spans, concurrency)
        passages += done
        made += summaries
    return BuildStats(len(ayat), passages, len(groups), made)
