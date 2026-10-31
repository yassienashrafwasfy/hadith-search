"""Profile the SQL each search method sends (docs/HANDOFF.md item 48).

    cd backend && DATABASE_URL=... python -m scripts.query_profile [--repeats 5] [--explain bm25]

For every search method and query below it runs the real search path (`run_search`, filters and
page size included) on the database in DATABASE_URL and prints the median time of the whole
search, the database time, the number of statements and the most rows one statement returned.
`--explain METHOD` also prints `EXPLAIN (ANALYZE, BUFFERS)` of every statement that method sent
for the last query. `--out FILE` writes the rows (and each result page's ids) as JSON, to compare
two versions of the code. Needs the full corpus to mean anything, and the Arabic encoder for the
dense methods (they are skipped without it). Use a copy of the database, not the live one.
"""

import argparse
import json
import statistics
import time

from sqlalchemy import event

import database
from features import Features
from models import SearchRequest
from services import retrieval

# (label, lang, query, grade filter, book filter): common words match ~30,000 hadiths.
QUERIES = [
    ("common", "en", "the messenger of allah said", None, None),
    ("mid", "en", "fasting in ramadan", None, None),
    ("rare", "en", "camel urine medicine", None, None),
    ("graded", "en", "the messenger of allah said", "Sahih", None),
    ("book", "en", "fasting in ramadan", None, "Sunan Abi Dawud"),
    ("common", "ar", "قال رسول الله صلى الله عليه وسلم", None, None),
    ("mid", "ar", "صيام رمضان", None, None),
    ("rare", "ar", "الحبة السوداء شفاء", None, None),
    ("graded", "ar", "قال رسول الله صلى الله عليه وسلم", "Hasan", None),
    ("book", "ar", "صيام رمضان", None, "Sahih al-Bukhari"),
]


class StatementLog:
    """Every statement the engine runs: SQL, parameters, milliseconds, rows."""

    def __init__(self, engine):
        self.rows: list[dict] = []
        event.listen(engine, "before_cursor_execute", self._before)
        event.listen(engine, "after_cursor_execute", self._after)

    @staticmethod
    def _before(conn, cursor, statement, params, context, executemany):
        conn.info["query_profile_t0"] = time.perf_counter()

    def _after(self, conn, cursor, statement, params, context, executemany):
        ms = (time.perf_counter() - conn.info["query_profile_t0"]) * 1000
        self.rows.append({"sql": statement, "params": params, "ms": ms, "rows": cursor.rowcount})


def _model():
    try:
        from scripts.loading import get_model

        model = get_model()
        return lambda: model
    except Exception as error:  # no exported model: profile the lexical methods only
        print(f"no Arabic encoder ({error}); skipping the dense methods")
        return None


def profile_one(session, log, system, case, model, repeats: int) -> dict:
    label, lang, query, grade, book = case
    request = SearchRequest(query=query, lang=lang, grade_filter=grade, book_filter=book)
    ctx = retrieval.SearchContext(session=session, model=model)
    retrieval.run_search(system, ctx, request)  # warm caches (corpus stats, query vector)
    times = []
    for _ in range(repeats):
        log.rows.clear()
        start = time.perf_counter()
        response = retrieval.run_search(system, ctx, request)
        times.append((time.perf_counter() - start) * 1000)
        session.rollback()
    return {
        "method": system.slug,
        "lang": lang,
        "query": label,
        "ms": round(statistics.median(times), 1),
        "db_ms": round(sum(row["ms"] for row in log.rows), 1),
        "statements": len(log.rows),
        "max_rows": max((row["rows"] for row in log.rows), default=0),
        "ids": [result.hadith.hadith_id for result in response.results],
    }


def explain(session, statements: list[dict]) -> None:
    connection = session.connection()
    for row in statements:
        print("-" * 100)
        print(row["sql"])
        plan = connection.exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS) " + row["sql"], row["params"])
        print("\n".join(line for (line,) in plan))
    session.rollback()


def main(argv=None) -> list[dict]:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--explain", metavar="METHOD")
    parser.add_argument("--out", metavar="FILE")
    args = parser.parse_args(argv)
    model = _model()
    systems = retrieval.enabled_systems(Features(dense_retrieval=model is not None))
    log = StatementLog(database.get_sync_engine())
    report = []
    with database.get_sync_session() as session:
        for system in systems:
            for case in QUERIES:
                if case[1].upper() in system.languages:
                    report.append(profile_one(session, log, system, case, model, args.repeats))
                    row = report[-1]
                    print(
                        f"{row['method']:18} {row['lang']} {row['query']:7} {row['ms']:7.1f} ms"
                        f"  db {row['db_ms']:6.1f} ms  {row['statements']} statements"
                        f"  max rows {row['max_rows']}"
                    )
            if system.slug == args.explain:
                explain(session, list(log.rows))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as out:
            json.dump(report, out, ensure_ascii=False, indent=1)
    return report


if __name__ == "__main__":
    main()
