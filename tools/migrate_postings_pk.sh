#!/usr/bin/env bash
# Gives an EXISTING database the covering primary key on `postings`:
# PRIMARY KEY (language, term, hadith_id) INCLUDE (tf), so ranking reads the term frequencies
# from the index alone (index-only scan; docs/HANDOFF.md item 48). A new database gets it from
# `init_schema` (create_all); an existing one does not, because create_all never changes a table.
#
#   tools/migrate_postings_pk.sh --dry-run    print every step and change nothing
#   DATABASE_URL=postgresql+psycopg://user:pass@host:5432/hadith tools/migrate_postings_pk.sh
#
# Safe to repeat (it stops when the key already includes tf) and additive: the same columns stay
# unique, so the old release keeps working. The new index is built CONCURRENTLY (reads and writes
# go on; about 4 s for 1.4 million postings), then one short transaction swaps the key over.
# Needs psql on the PATH (or set PSQL, e.g. PSQL="docker compose exec -T postgres psql -U hadith").
#
# Keep the index statement below in step with backend/models/orm.py (Posting);
# tests/test_migrate_postings_pk.py compares them.
set -euo pipefail
cd "$(dirname "$0")/.."

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h | --help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $arg (only --dry-run)" >&2; exit 2 ;;
  esac
done

PSQL=${PSQL:-psql}
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

echo "postings primary key migration ($([ "$DRY_RUN" = 1 ] && echo 'dry run, nothing is changed' || echo 'applying'))"

COVERED="SELECT indnatts > indnkeyatts FROM pg_index WHERE indexrelid = 'postings_pkey'::regclass"
if [ "$DRY_RUN" = 0 ]; then
  # shellcheck disable=SC2086
  if [ "$($PSQL "$URL" -v ON_ERROR_STOP=1 -tAc "$COVERED")" = t ]; then
    echo "postings_pkey already includes tf; nothing to do"
    exit 0
  fi
fi

# A run stopped half way can leave an invalid index behind; start that step again.
run_sql "DROP INDEX CONCURRENTLY IF EXISTS postings_pkey_covering"
run_sql "CREATE UNIQUE INDEX CONCURRENTLY postings_pkey_covering ON postings (language, term, hadith_id) INCLUDE (tf)"
run_sql "ALTER TABLE postings DROP CONSTRAINT postings_pkey, ADD CONSTRAINT postings_pkey PRIMARY KEY USING INDEX postings_pkey_covering"
run_sql "ANALYZE postings"

echo "done"
