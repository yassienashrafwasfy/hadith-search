"""Fixtures of the Quran tests: a 3-surah mini corpus in real PostgreSQL schemas and a fake encoder.

Use `_quran_session` (sync Session; sources `quran` and `tafsir_mini` already filled), `_fake_encoder`
(deterministic 64-dim unit vectors), `_mini_corpus` (the plain data) and `_quran_sources` (their
`SourceInfo`s). Every test gets its own schemas, so xdist is safe. The verses are a small hand-typed
sample for tests only, not the Tanzil file.
"""

import hashlib
import math
from dataclasses import dataclass

import pytest
from sqlalchemy import inspect
from sqlalchemy.schema import DropSchema

from quran import models
from quran.db import add_nodes, create_source
from quran.types import Level, SourceInfo

DIM = 64


class _FakeEncoder:
    """Same text, same vector: a 64-dim unit vector seeded by SHA-256 of the text."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        values = []
        counter = 0
        while len(values) < DIM:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            values.extend((b - 127.5) / 127.5 for b in digest)
            counter += 1
        values = values[:DIM]
        norm = math.sqrt(sum(v * v for v in values))
        return [v / norm for v in values]


@dataclass(frozen=True)
class MiniAyah:
    surah: int
    ayah: int
    text: str
    juz: int
    place: str


# (surah, place, [verses]); surahs 1, 112 and 114
_SURAHS = {
    1: (
        "meccan",
        [
            "بِسْمِ ٱللَّهِ ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ",
            "ٱلْحَمْدُ لِلَّهِ رَبِّ ٱلْعَـٰلَمِينَ",
            "ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ",
            "مَـٰلِكِ يَوْمِ ٱلدِّينِ",
            "إِيَّاكَ نَعْبُدُ وَإِيَّاكَ نَسْتَعِينُ",
            "ٱهْدِنَا ٱلصِّرَٰطَ ٱلْمُسْتَقِيمَ",
            "صِرَٰطَ ٱلَّذِينَ أَنْعَمْتَ عَلَيْهِمْ غَيْرِ ٱلْمَغْضُوبِ عَلَيْهِمْ وَلَا ٱلضَّآلِّينَ",
        ],
    ),
    112: (
        "meccan",
        [
            "قُلْ هُوَ ٱللَّهُ أَحَدٌ",
            "ٱللَّهُ ٱلصَّمَدُ",
            "لَمْ يَلِدْ وَلَمْ يُولَدْ",
            "وَلَمْ يَكُن لَّهُۥ كُفُوًا أَحَدٌۢ",
        ],
    ),
    114: (
        "meccan",
        [
            "قُلْ أَعُوذُ بِرَبِّ ٱلنَّاسِ",
            "مَلِكِ ٱلنَّاسِ",
            "إِلَـٰهِ ٱلنَّاسِ",
            "مِن شَرِّ ٱلْوَسْوَاسِ ٱلْخَنَّاسِ",
            "ٱلَّذِى يُوَسْوِسُ فِى صُدُورِ ٱلنَّاسِ",
            "مِنَ ٱلْجِنَّةِ وَٱلنَّاسِ",
        ],
    ),
}
_JUZ = {1: 1, 112: 30, 114: 30}
_STRIP = dict.fromkeys(map(ord, "ًٌٍَُِّْٰٓۥۢ"))


def _simple(text: str) -> str:
    return text.translate(_STRIP).replace("ٱ", "ا")


@pytest.fixture
def _mini_corpus() -> list[MiniAyah]:
    return [
        MiniAyah(s, i, text, _JUZ[s], place)
        for s, (place, verses) in _SURAHS.items()
        for i, text in enumerate(verses, 1)
    ]


@pytest.fixture
def _quran_sources() -> dict[str, SourceInfo]:
    return {
        "quran": SourceInfo(
            "quran", "نص القرآن الكريم", ("ar",), ("surah", "ayah_from", "ayah_to", "juz", "place")
        ),
        "tafsir_mini": SourceInfo(
            "tafsir_mini",
            "تفسير مختصر",
            ("ar",),
            ("surah", "ayah_from", "ayah_to", "author"),
            ("muyassar",),
        ),
    }


@pytest.fixture
def _fake_encoder() -> _FakeEncoder:
    return _FakeEncoder()


@pytest.fixture
def _quran_schemas(_pg_schema, monkeypatch):
    """Unique schema names for this test's sources; every schema under the prefix is dropped after."""
    import database

    prefix = f"{_pg_schema}_"
    monkeypatch.setattr(models, "SCHEMA_PREFIX", prefix)
    yield prefix
    with database.get_sync_session() as session:
        for schema in inspect(session.connection()).get_schema_names():
            if schema.startswith(prefix):
                session.execute(DropSchema(schema, cascade=True))
        session.commit()


@pytest.fixture
def _quran_empty_session(_quran_schemas):
    """A sync Session with unique source schemas and nothing created yet (no catalog either)."""
    import database

    with database.get_sync_session() as session:
        yield session


def _node(level, text, encoder, **meta):
    return {
        "level": level,
        "text": text,
        "search_text": _simple(text),
        "embedding": encoder.encode([_simple(text)])[0],
        **meta,
    }


def _fill_quran(session, corpus, encoder):
    """Per surah: a surah node, one passage (all ayat), and the ayat as leaves (parent = passage)."""
    for surah in sorted({a.surah for a in corpus}):
        ayat = [a for a in corpus if a.surah == surah]
        meta = dict(surah=surah, juz=ayat[0].juz, place=ayat[0].place)
        joined = " ".join(a.text for a in ayat)
        (surah_id,) = add_nodes(
            session,
            "quran",
            [_node(Level.SURAH, joined, encoder, ayah_from=1, ayah_to=len(ayat), **meta)],
        )
        (passage_id,) = add_nodes(
            session,
            "quran",
            [
                _node(
                    Level.PASSAGE,
                    joined,
                    encoder,
                    parent_id=surah_id,
                    ayah_from=1,
                    ayah_to=len(ayat),
                    summary="ملخص",
                    **meta,
                )
            ],
        )
        add_nodes(
            session,
            "quran",
            [
                _node(
                    Level.AYAH,
                    a.text,
                    encoder,
                    parent_id=passage_id,
                    ayah_from=a.ayah,
                    ayah_to=a.ayah,
                    **meta,
                )
                for a in ayat
            ],
        )


def _fill_tafsir(session, corpus, encoder):
    """One tafsir leaf per ayah of surah 112, by author `muyassar`."""
    add_nodes(
        session,
        "tafsir_mini",
        [
            _node(
                Level.AYAH,
                f"تفسير {a.text}",
                encoder,
                surah=a.surah,
                ayah_from=a.ayah,
                ayah_to=a.ayah,
                juz=a.juz,
                author="muyassar",
            )
            for a in corpus
            if a.surah == 112
        ],
    )


@pytest.fixture
def _quran_session(_quran_empty_session, _quran_sources, _mini_corpus, _fake_encoder):
    """Session on a database holding the `quran` and `tafsir_mini` sources (committed)."""
    session = _quran_empty_session
    for info in _quran_sources.values():
        create_source(session, info)
    _fill_quran(session, _mini_corpus, _fake_encoder)
    _fill_tafsir(session, _mini_corpus, _fake_encoder)
    session.commit()
    return session
