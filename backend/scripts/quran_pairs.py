"""Export the Quran (and optionally hadith) corpus as JSONL for the Colab fine-tuning notebook.

    python scripts/quran_pairs.py --out data/quran/corpus.jsonl [--holdout data/quran/holdout.json] \
        [--hadith]

One row per ayah of every source: the original text exactly as stored (Tanzil must not change)
and `encoding`, the cleaned text production embeds. `--hadith` adds one row per hadith (`source` "hadith": `surah` is a chapter number that is unique
across books, `ayah` is the hadith id, `text` the Arabic matn, plus `chapter_title`); the
notebook holds out whole chapters itself. `--holdout` takes the evaluation refs
(`[[surah, ayah], ...]`); their rows are written with `"held_out": true` so the notebook never
trains on them.
"""

import argparse
import json
import os
import sys
from collections.abc import Iterable, Iterator
from itertools import chain

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

Ref = tuple[int, int]


def _row(source: str, node, held_out: bool) -> dict:
    return {
        "source": source,
        "author": node.author,
        "surah": node.surah,
        "ayah": node.ayah_from,
        "text": node.text,
        "encoding": node.search_text,
        "held_out": held_out,
    }


def corpus_rows(session, sources: Iterable[str], holdout: set[Ref]) -> Iterator[dict]:
    """Ayah rows of each source in (surah, ayah) order, flagged when the ayah is held out."""
    from sqlalchemy import select

    from quran.models import node_table
    from quran.types import Level

    for source in sources:
        table = node_table(source)
        query = (
            select(table)
            .where(table.c.level == Level.AYAH.value)
            .order_by(table.c.surah, table.c.ayah_from)
        )
        for node in session.execute(query):
            yield _row(source, node, (node.surah, node.ayah_from) in holdout)


def _clean(value) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _chapter_key(book, number) -> tuple[str, float]:
    return (book if isinstance(book, str) else "", number if number == number else -1)


def hadith_rows(frame, encode_text) -> Iterator[dict]:
    """Rows of a hadiths DataFrame (id, Book, Chapter_Number, Arabic_Matn, Chapter_Title_Arabic).

    A chapter is a (Book, Chapter_Number) pair; its number here counts them from 1 in sorted order,
    so it is stable for one corpus. Hadiths without an Arabic matn are left out.
    """
    keys = [_chapter_key(b, n) for b, n in zip(frame["Book"], frame["Chapter_Number"])]
    numbers = {key: i for i, key in enumerate(sorted(set(keys)), start=1)}
    for key, row in zip(keys, frame.itertuples()):
        matn = _clean(row.Arabic_Matn)
        if not matn:
            continue
        yield {
            "source": "hadith",
            "author": key[0],
            "surah": numbers[key],
            "ayah": int(row.id),
            "text": matn,
            "encoding": encode_text(matn),
            "chapter_title": _clean(row.Chapter_Title_Arabic),
            "held_out": False,
        }


def read_holdout(path: str | None) -> set[Ref]:
    if not path:
        return set()
    with open(path, encoding="utf-8") as handle:
        return {(int(s), int(a)) for s, a in json.load(handle)}


def write_jsonl(rows: Iterable[dict], path: str) -> int:
    count = 0
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def _hadith_from_db() -> Iterator[dict]:
    from database import read_hadiths_df
    from scripts.arabic_encoder import encoding_text

    frame = read_hadiths_df()[
        ["id", "Book", "Chapter_Number", "Arabic_Matn", "Chapter_Title_Arabic"]
    ]
    return hadith_rows(frame, encoding_text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--holdout", help="JSON list of [surah, ayah] used only for evaluation")
    parser.add_argument("--source", action="append", help="repeat; default: every source")
    parser.add_argument("--hadith", action="store_true", help="also export the hadith corpus")
    args = parser.parse_args(argv)
    from database import get_sync_session
    from quran.db import list_sources

    with get_sync_session() as session:
        names = args.source or [s.name for s in list_sources(session)]
        rows = corpus_rows(session, names, read_holdout(args.holdout))
        count = write_jsonl(chain(rows, _hadith_from_db() if args.hadith else ()), args.out)
    print(f"wrote {count} rows to {args.out}")
    return count


if __name__ == "__main__":
    main()
