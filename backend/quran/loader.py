"""Parse Tanzil text, Tanzil metadata and a per-ayah tafsir SQLite, and insert ayah rows.

The Tanzil text is stored byte-exact (its licence forbids changes). Search text, embeddings and
summaries are filled in later by the ingest step.
"""

import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, insert, select
from sqlalchemy.exc import NoSuchTableError

from quran.types import Level, Place

TANZIL_NOTICE = """\
Tanzil Quran Text (Uthmani)
Copyright (C) 2007-2021 Tanzil Project
License: Creative Commons Attribution 3.0 (https://creativecommons.org/licenses/by/3.0/)

This copy of the Quran text is carefully produced, highly verified and continuously monitored
by a group of specialists at Tanzil Project.

TERMS OF USE:

- Permission is granted to copy and distribute verbatim copies of this text, but CHANGING IT IS
  NOT ALLOWED.
- This Quran text can be used in any website or application, provided that its source
  (Tanzil.net) is clearly indicated, and a link is made to tanzil.net to enable users to keep
  track of changes.
- This copyright notice shall be included in all verbatim copies of the text, and shall be
  reproduced appropriately in all files derived from or containing substantial portion of this
  text.

Please check updates at: http://tanzil.net/updates/
"""

TAFSIR_NOTICE = """\
Tafsir dataset: Tafsir Center for Quranic Studies (tafsir.net)
License: Creative Commons Attribution 4.0 (https://creativecommons.org/licenses/by/4.0/)
Attribution: Tafsir Center for Quranic Studies (tafsir.net)
"""


class LoaderError(ValueError):
    pass


@dataclass(frozen=True)
class RawAyah:
    surah: int
    ayah: int
    text: str  # exactly as in the Tanzil file


@dataclass(frozen=True)
class RawTafsir:
    surah: int
    ayah: int
    text: str


@dataclass(frozen=True)
class QuranMeta:
    """Metadata from quran-data.xml. `juz_starts` is sorted (juz, surah, ayah)."""

    places: dict[int, Place]
    juz_starts: tuple[tuple[int, int, int], ...]

    def juz_of(self, surah: int, ayah: int) -> int | None:
        found = None
        for juz, s, a in self.juz_starts:
            if (s, a) <= (surah, ayah):
                found = juz
            else:
                break
        return found

    def place_of(self, surah: int) -> Place | None:
        return self.places.get(surah)


def _parse_tanzil_line(line: str, number: int) -> RawAyah:
    parts = line.split("|", 2)
    if len(parts) != 3:
        raise LoaderError(f"line {number}: expected surah|ayah|text")
    try:
        return RawAyah(int(parts[0]), int(parts[1]), parts[2])
    except ValueError as exc:
        raise LoaderError(f"line {number}: surah and ayah must be integers") from exc


def iter_tanzil(path: str | Path) -> Iterator[RawAyah]:
    """Yield ayat from a Tanzil pipe file. Comment lines (#) and blank lines are skipped."""
    with open(path, encoding="utf-8", newline="") as handle:
        for number, raw in enumerate(handle, start=1):
            line = raw.rstrip("\r\n")
            if line and not line.startswith("#"):
                yield _parse_tanzil_line(line, number)


def parse_tanzil(path: str | Path) -> list[RawAyah]:
    return list(iter_tanzil(path))


def _place(value: str) -> Place | None:
    try:
        return Place(value.strip().lower())
    except ValueError:
        return None


def parse_metadata(path: str | Path) -> QuranMeta:
    root = ET.parse(path).getroot()
    places = {}
    for sura in root.iter("sura"):
        place = _place(sura.get("type", ""))
        if place is not None:
            places[int(sura.get("index"))] = place
    starts = sorted(
        (int(j.get("index")), int(j.get("sura")), int(j.get("aya"))) for j in root.iter("juz")
    )
    return QuranMeta(places, tuple(starts))


def parse_tafsir(
    path: str | Path,
    table: str = "tafsir",
    surah_col: str = "sura",
    ayah_col: str = "aya",
    text_col: str = "text",
) -> list[RawTafsir]:
    """Read one tafsir from a SQLite file with one row per ayah (read-only, reflected)."""
    engine = create_engine(f"sqlite:///file:{Path(path).resolve()}?mode=ro&uri=true")
    try:
        meta = MetaData()
        try:
            tbl = Table(table, meta, autoload_with=engine)
            cols = [tbl.c[surah_col], tbl.c[ayah_col], tbl.c[text_col]]
        except (NoSuchTableError, KeyError) as exc:
            raise LoaderError(f"tafsir table or column not found: {exc}") from exc
        with engine.connect() as conn:
            rows = conn.execute(select(*cols).order_by(cols[0], cols[1])).all()
    finally:
        engine.dispose()
    return [RawTafsir(int(s), int(a), t) for s, a, t in rows if t]


def _identity(text: str) -> str:
    return text


def quran_rows(
    ayat: Iterable[RawAyah],
    meta: QuranMeta | None = None,
    search_text: Callable[[str], str] = _identity,
) -> list[dict]:
    """Ayah rows. `text` is the original; `search_text` is derived (e.g. diacritics removed)."""
    rows = []
    for item in ayat:
        place = meta.place_of(item.surah) if meta else None
        rows.append(
            {
                "parent_id": None,
                "level": Level.AYAH.value,
                "text": item.text,
                "search_text": search_text(item.text),
                "surah": item.surah,
                "ayah_from": item.ayah,
                "ayah_to": item.ayah,
                "juz": meta.juz_of(item.surah, item.ayah) if meta else None,
                "place": place.value if place else None,
                "author": None,
            }
        )
    return rows


def tafsir_rows(
    entries: Iterable[RawTafsir],
    author: str,
    meta: QuranMeta | None = None,
    search_text: Callable[[str], str] = _identity,
) -> list[dict]:
    rows = quran_rows((RawAyah(e.surah, e.ayah, e.text) for e in entries), meta, search_text)
    for row in rows:
        row["author"] = author
    return rows


def insert_rows(session, table, rows: list[dict], batch: int = 1000) -> int:
    """Insert ayah rows into a source's node table (ORM class or Table). Caller commits."""
    for start in range(0, len(rows), batch):
        session.execute(insert(table), rows[start : start + batch])
    return len(rows)


def load_quran(
    session,
    table,
    tanzil: str | Path,
    metadata: str | Path | None = None,
    search_text: Callable[[str], str] = _identity,
) -> int:
    meta = parse_metadata(metadata) if metadata else None
    return insert_rows(session, table, quran_rows(iter_tanzil(tanzil), meta, search_text))


def load_tafsir(
    session,
    table,
    sqlite_path: str | Path,
    author: str,
    metadata: str | Path | None = None,
    search_text: Callable[[str], str] = _identity,
    sqlite_table: str = "tafsir",
    **cols,
) -> int:
    """`table` is the node table to fill; `sqlite_table` is the table inside the SQLite file."""
    meta = parse_metadata(metadata) if metadata else None
    entries = parse_tafsir(sqlite_path, table=sqlite_table, **cols)
    return insert_rows(session, table, tafsir_rows(entries, author, meta, search_text))


def write_licenses(directory: str | Path, notices: Iterable[str] = (TANZIL_NOTICE,)) -> Path:
    """Write LICENSES.md (one section per notice); the Tanzil notice is kept verbatim."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / "LICENSES.md"
    sections = [f"```\n{n.rstrip()}\n```" for n in notices]
    path.write_text("# Licenses of the Quran data\n\n" + "\n\n".join(sections) + "\n", "utf-8")
    return path


def default_licenses(with_tafsir: bool) -> tuple[str, ...]:
    return (TANZIL_NOTICE, TAFSIR_NOTICE) if with_tafsir else (TANZIL_NOTICE,)
