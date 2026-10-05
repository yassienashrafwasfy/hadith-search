"""Rebuild the exact-search text (`hadith_exact_text`) from the hadiths. Also runs as part of
`build_inverted_index`; this entry point is for an existing database (tools/migrate_trgm.sh)."""

from database import get_sync_session, init_schema_sync
from services.exact_text import rebuild


def run():
    init_schema_sync()
    with get_sync_session() as session:
        count = rebuild(session)
        session.commit()
    print(f"Exact-search text rebuilt for {count} hadiths")


if __name__ == "__main__":
    run()
