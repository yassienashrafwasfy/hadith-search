#!/usr/bin/env bash
# Adds the pg_trgm extension, the exact-search table and the trigram indexes to an EXISTING
# database. A new database gets all of this from `init_schema` (create_all); an existing one
# does not, because create_all never changes a table that is already there.
#
#   tools/migrate_trgm.sh --dry-run    print every step and change nothing
#   DATABASE_URL=postgresql+psycopg://user:pass@host:5432/hadith tools/migrate_trgm.sh
#
# Safe to repeat (every step is IF NOT EXISTS or a rebuild) and additive: the old release keeps
# working on the result, so run it before or after deploying the new release (docs/HANDOFF.md
# items 21 and 43). The indexes are built CONCURRENTLY, so reads and writes are not blocked;
# that is why each statement is its own psql call (CONCURRENTLY cannot run in a transaction).
# Needs psql on the PATH (or set PSQL, e.g. PSQL="docker compose exec -T postgres psql -U hadith")
# and the repo .venv (or set PYTHON) for the one backfill step.
#
# Keep the index statements below in step with backend/models/orm.py;
# tests/test_migrate_trgm.py compares them with what create_all would build.
set -euo pipefail
cd "$(dirname "$0")/.."

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h | --help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $arg (only --dry-run)" >&2; exit 2 ;;
  esac
done

PSQL=${PSQL:-psql}
if [ -z "${PYTHON:-}" ]; then
  if [ -x .venv/bin/python ]; then PYTHON=$PWD/.venv/bin/python; else PYTHON=python3; fi
fi

if [ "$DRY_RUN" = 1 ]; then
  URL='<DATABASE_URL>'
else
  : "${DATABASE_URL:?set DATABASE_URL to the database to migrate}"
  URL=${DATABASE_URL/+psycopg/} # psql does not know the SQLAlchemy driver suffix
fi

run_sql() {
  echo "-> $1"
  if [ "$DRY_RUN" = 0 ]; then
    # shellcheck disable=SC2086
    $PSQL "$URL" -v ON_ERROR_STOP=1 -c "$1"
  fi
}

echo "pg_trgm migration ($([ "$DRY_RUN" = 1 ] && echo 'dry run, nothing is changed' || echo 'applying'))"

run_sql "CREATE EXTENSION IF NOT EXISTS pg_trgm"

run_sql "CREATE TABLE IF NOT EXISTS hadith_exact_text (hadith_id INTEGER NOT NULL PRIMARY KEY REFERENCES hadiths (id) ON DELETE CASCADE, english TEXT NOT NULL DEFAULT '  ', arabic TEXT NOT NULL DEFAULT '  ')"

# Fill the table from the hadiths (the same SQL the index build uses). It is built before its
# indexes, which is faster than the other way round.
echo "-> backfill hadith_exact_text (python -m scripts.build_exact_text)"
if [ "$DRY_RUN" = 0 ]; then
  (cd backend && "$PYTHON" -m scripts.build_exact_text)
fi

run_sql "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_hadith_exact_text_english_trgm ON hadith_exact_text USING gin (english gin_trgm_ops)"
run_sql "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_hadith_exact_text_arabic_trgm ON hadith_exact_text USING gin (arabic gin_trgm_ops)"
run_sql "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_terms_term_trgm ON terms USING gin (term gin_trgm_ops)"
run_sql "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_chapters_title_english_trgm ON chapters USING gin (title_english gin_trgm_ops)"
run_sql "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_chapters_title_arabic_trgm ON chapters USING gin (title_arabic gin_trgm_ops)"

echo "done"
