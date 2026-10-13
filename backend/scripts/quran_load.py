"""Load Tanzil text (and optionally a tafsir) into a Quran source store.

    python scripts/quran_load.py --source quran --tanzil data/quran-uthmani.txt \
        --metadata data/quran-data.xml
    python scripts/quran_load.py --source ibn_kathir --tafsir data/tafsir.db --author "Ibn Kathir"

Data files are never committed. Text is stored exactly as downloaded.
"""

import argparse
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from quran import loader  # noqa: E402
from quran.types import SourceInfo  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", required=True, help="source (schema) name")
    p.add_argument("--tanzil", help="Tanzil pipe-format Uthmani text")
    p.add_argument("--metadata", help="Tanzil quran-data.xml (juz, Meccan/Medinan)")
    p.add_argument("--tafsir", help="per-ayah tafsir SQLite file")
    p.add_argument("--author", help="tafsir author (required with --tafsir)")
    p.add_argument("--description", help="what the router reads to pick this source")
    p.add_argument("--tafsir-table", default="tafsir")
    p.add_argument("--surah-col", default="sura")
    p.add_argument("--ayah-col", default="aya")
    p.add_argument("--text-col", default="text")
    p.add_argument("--licenses-dir", default=os.path.join(BACKEND_DIR, "data", "quran"))
    return p


def source_info(args: argparse.Namespace) -> SourceInfo:
    """The catalog row: a tafsir adds the author filter."""
    if args.tafsir:
        description = args.description or f"تفسير {args.author} لآيات القرآن الكريم"
        return SourceInfo(
            args.source, description, ("ar",), ("surah", "ayah", "juz", "author"), (args.author,)
        )
    description = args.description or "نص القرآن الكريم (الرسم العثماني) مع السور والآيات"
    return SourceInfo(args.source, description, ("ar",), ("surah", "ayah", "juz", "place"))


def run(args: argparse.Namespace) -> int:
    if bool(args.tanzil) == bool(args.tafsir):
        raise SystemExit("give exactly one of --tanzil or --tafsir")
    if args.tafsir and not args.author:
        raise SystemExit("--tafsir needs --author")
    from database import get_sync_session
    from quran.db import create_source
    from quran.models import node_table
    from scripts.arabic_encoder import encoding_text

    table = node_table(args.source)
    with get_sync_session() as session:
        create_source(session, source_info(args))
        if args.tanzil:
            count = loader.load_quran(session, table, args.tanzil, args.metadata, encoding_text)
        else:
            cols = {
                "sqlite_table": args.tafsir_table,
                "surah_col": args.surah_col,
                "ayah_col": args.ayah_col,
                "text_col": args.text_col,
            }
            count = loader.load_tafsir(
                session, table, args.tafsir, args.author, args.metadata, encoding_text, **cols
            )
        session.commit()
    loader.write_licenses(args.licenses_dir, loader.default_licenses(bool(args.tafsir)))
    print(f"loaded {count} ayah rows into {args.source}")
    return count


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    main()
