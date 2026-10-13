"""Shared types of the Quran RAG package. Every module codes against these, not each other."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

FALLBACK_SENTENCE = "عذرًا، لا تتوفر لدي معلومات كافية"


class Level(StrEnum):
    AYAH = "ayah"
    PASSAGE = "passage"
    SURAH = "surah"


class Place(StrEnum):
    MECCAN = "meccan"
    MEDINAN = "medinan"


@dataclass(frozen=True)
class NodeRef:
    """A node in one source's store. `ref` is stable text such as "quran:2:255" or "ibn_kathir:p:12"."""

    source: str
    node_id: int
    level: Level
    text: str  # ORIGINAL text, never the summary
    surah: int | None = None
    ayah_from: int | None = None
    ayah_to: int | None = None
    score: float = 0.0


@dataclass(frozen=True)
class Filters:
    """Validated self-query filters. None means no constraint."""

    surah: int | None = None
    ayah_from: int | None = None
    ayah_to: int | None = None
    juz: int | None = None
    place: Place | None = None
    author: str | None = None


@dataclass(frozen=True)
class SourceInfo:
    """One row of the `sources` catalog the router reads."""

    name: str  # also the schema name
    description: str
    languages: tuple[str, ...]
    fields: tuple[str, ...]  # metadata fields this source can filter on
    authors: tuple[str, ...] = ()


@dataclass(frozen=True)
class WebPassage:
    url: str
    text: str


@dataclass
class Citation:
    ref: str
    source: str
    text: str  # inserted from the DB or fetched page by code, never written by the LLM
    web: bool = False
    attribution: str = ""


@dataclass
class Answer:
    text: str
    citations: list[Citation] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    fallback: bool = False


class LLMClient(Protocol):
    """Constrained-decoding client. `schema` is a JSON schema; returns the parsed object.

    Raises `LLMError` on timeout, HTTP failure or invalid JSON.
    """

    async def generate_json(self, prompt: str, schema: dict) -> dict: ...

    async def generate_text(self, prompt: str, max_tokens: int = 256) -> str: ...


class LLMError(Exception):
    pass


class Encoder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...
