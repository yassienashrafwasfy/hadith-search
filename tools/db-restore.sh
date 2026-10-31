#!/usr/bin/env bash
# Restores PostgreSQL from the db-backup service's backups (docs/HANDOFF.md item 49).
#
#   tools/db-restore.sh --dry-run                    print the plan, change nothing
#   tools/db-restore.sh --yes                        restore up to the newest WAL (disk lost)
#   tools/db-restore.sh --yes --target-time '2026-10-31 14:05:00+00'
#                                                    restore to just before that time (a mistake
#                                                    such as DROP TABLE); `@<unix seconds>` works too
#   tools/db-restore.sh --yes --discard-current      do not keep a copy of the current data dir
#
# Steps: stop db-backup and postgres, keep the current data directory in /backups/pre-restore/
# (unless --discard-current, or there is none; the newest PRE_RESTORE_KEEP=2 are kept, zstd),
# unpack the newest base backup that finished before the target, replay the WAL up to the
# target, promote. The app keeps running: it answers 503 while the database is away and
# reconnects by itself. The restored server starts read-only and is
# opened for writes only once the backup slot exists again, so the WAL stream misses nothing.
#
# Acts on the compose project in the environment (COMPOSE_PROJECT_NAME / COMPOSE_FILE), so
# tools/dr/drill.py runs the very same script against the drill stack.
set -euo pipefail
trap 'echo "error: tools/db-restore.sh line $LINENO failed (exit $?)" >&2' ERR
cd "$(dirname "$0")/.."

DRY_RUN=0
YES=0
TARGET=""
DISCARD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --yes) YES=1 ;;
    --discard-current) DISCARD=1 ;;
    --target-time) TARGET=${2:?--target-time needs a time}; shift ;;
    -h | --help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

COMPOSE=(docker compose)
log() { echo "$(date -u +%T) restore: $*"; }
run() {
  log "$*"
  [ "$DRY_RUN" = 1 ] || "$@"
}
die() { echo "error: $*" >&2; exit 1; }
psql_db() { "${COMPOSE[@]}" exec -T postgres psql -q -U hadith -d hadith -v ON_ERROR_STOP=1 "$@"; }

# Runs a shell snippet as the postgres user with the data and backup volumes mounted.
in_db_volumes() {
  "${COMPOSE[@]}" run --rm --no-deps -T --user postgres --entrypoint bash postgres -c "$1"
}

if [ -n "$TARGET" ] && [[ "$TARGET" == @* ]]; then
  # Keep the fraction as written: date's %N turned @1.068 into 1.68 here.
  SECONDS_PART=${TARGET#@}
  FRACTION=""
  [[ "$SECONDS_PART" == *.* ]] && FRACTION=".${SECONDS_PART#*.}"
  TARGET="$(date -u -d "@${SECONDS_PART%.*}" '+%Y-%m-%d %H:%M:%S')${FRACTION}+00"
fi
TARGET_EPOCH=$([ -n "$TARGET" ] && date -u -d "$TARGET" +%s || date +%s)

# The newest base backup that was finished before the target. A backup is consistent only from
# its end, so one that started before the target but finished after it cannot stop there
# (PostgreSQL refuses: "requested recovery stop point is before consistent recovery point").
# backup_manifest is the last file pg_basebackup writes; its time is the finish time.
pick_base() {
  in_db_volumes 'for d in /backups/base/*Z; do
      [ -f "$d/backup_manifest" ] && echo "$(basename "$d") $(stat -c %Y "$d/backup_manifest")"
    done 2>/dev/null; true' |
    while read -r name end; do
      # if, not &&: a skipped last backup would end the loop with 1 and set -e would exit.
      if [ "$end" -lt "$TARGET_EPOCH" ]; then echo "$name"; fi
    done | sort | tail -n 1
}

BASE=$(pick_base)
[ -n "$BASE" ] || die "no base backup before ${TARGET:-now} in /backups/base (is db-backup running?)"
log "base backup $BASE, target ${TARGET:-the newest WAL}"
if [ "$DRY_RUN" = 0 ] && [ "$YES" = 0 ]; then
  die "this replaces the live database; run again with --yes (or --dry-run to see the plan)"
fi

START=$(date +%s.%N)
run "${COMPOSE[@]}" stop -t 10 db-backup postgres

# zstd: a plain tar of 350 MB took 18 s here, the compressed one 2.3 s (and a quarter of the
# space). Only the newest PRE_RESTORE_KEEP copies are kept; they used to pile up.
KEEP_CMD="mkdir -p /backups/pre-restore
find /backups/pre-restore -maxdepth 1 -name '*.tar*' | sort -r | tail -n +${PRE_RESTORE_KEEP:-2} | xargs -r rm -f
tar -cf - -C /var/lib/postgresql/data . | zstd -1 -T0 -q -o /backups/pre-restore/\$(date -u +%Y%m%dT%H%M%SZ).tar.zst"
[ "$DISCARD" = 1 ] && KEEP_CMD=true
# Read-only until the slot exists again (see the end); reset with the recovery settings.
RECOVERY="restore_command = '/backup/restore-wal.sh %f %p'
recovery_target_action = 'promote'
default_transaction_read_only = on"
[ -n "$TARGET" ] && RECOVERY="$RECOVERY
recovery_target_time = '$TARGET'
recovery_target_inclusive = off"
UNPACK="set -eo pipefail
cd /var/lib/postgresql/data
if [ -f PG_VERSION ]; then
$KEEP_CMD
fi
find . -mindepth 1 -delete
tar --zstd -xf /backups/base/$BASE/base.tar.zst
rm -rf pg_wal/* && mkdir -p pg_wal/archive_status
cat >> postgresql.auto.conf <<'EOF'
$RECOVERY
EOF
touch recovery.signal
chmod 700 .
touch /backups/.need-base # db-backup takes a fresh base backup on the new timeline"
log "unpack $BASE into the data volume and write the recovery settings"
[ "$DRY_RUN" = 1 ] || in_db_volumes "$UNPACK"

run "${COMPOSE[@]}" up -d --no-deps --wait postgres
if [ "$DRY_RUN" = 0 ]; then
  log "waiting for the WAL replay to finish"
  until [ "$(psql_db -tAc 'select pg_is_in_recovery()' 2>/dev/null)" = f ]; do sleep 0.1; done
  # The slot did not survive (base backups do not copy slots). Create it while the server is
  # still read-only, so the WAL stream of the new timeline starts before the first write.
  psql_db -c "SELECT pg_create_physical_replication_slot('backup', true)
              WHERE NOT EXISTS (SELECT FROM pg_replication_slots WHERE slot_name = 'backup')" >/dev/null
  psql_db -c "ALTER SYSTEM RESET restore_command" -c "ALTER SYSTEM RESET recovery_target_action" \
    -c "ALTER SYSTEM RESET recovery_target_time" -c "ALTER SYSTEM RESET recovery_target_inclusive" \
    -c "ALTER SYSTEM RESET default_transaction_read_only" -c "SELECT pg_reload_conf()" >/dev/null
  log "the database takes writes again"
fi
run "${COMPOSE[@]}" up -d --no-deps db-backup
log "done in $(awk -v a="$START" -v b="$(date +%s.%N)" 'BEGIN { printf "%.1f", b - a }') s"
