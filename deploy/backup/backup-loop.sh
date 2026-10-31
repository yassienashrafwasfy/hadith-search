#!/usr/bin/env bash
# Continuous PostgreSQL backups (the db-backup service; docs/HANDOFF.md item 49).
#
# - pg_receivewal streams the WAL into /backups/wal/tl<timeline> as it is written (--synchronous: each piece is
#   flushed to disk before the server is told), through the replication slot `backup`, so a lost
#   database disk loses well under a second of committed data. The slot makes the server keep WAL
#   the stream has not received yet; max_slot_wal_keep_size caps that if this service is down.
# - pg_basebackup takes a zstd-compressed base backup into /backups/base/<UTC time> every
#   BACKUP_INTERVAL_SECONDS (and at once when there is none, or after a restore asks for one by
#   creating /backups/.need-base). The newest BACKUP_KEEP are kept, with the WAL they need.
# - /backups/status.env says when the last base backup finished and how big it is.
#
# tools/db-restore.sh restores from these. /backups is a volume on the same host: copy it off the
# host as well (see the HANDOFF item), or a lost host loses the backups with the database.
set -euo pipefail

# A new volume is owned by root: hand it to postgres (the server reads the WAL from it when it is
# restored), then go on as postgres.
if [ "$(id -u)" = 0 ]; then
  chown postgres:postgres /backups
  exec gosu postgres "$0" "$@"
fi

: "${PGHOST:?}" "${PGUSER:?}" "${PGPASSWORD:?}"
INTERVAL=${BACKUP_INTERVAL_SECONDS:-21600}
KEEP=${BACKUP_KEEP:-3}
SLOT=${BACKUP_SLOT:-backup}
BASE=/backups/base
WAL=/backups/wal
mkdir -p "$BASE" "$WAL"

log() { echo "$(date -u +%FT%TZ) backup: $*"; }

wait_for_server() {
  until pg_isready -q -d postgres; do sleep 1; done
}

ensure_slot() {
  # --if-not-exists: a restart (or a restored database with no slot yet) is fine either way.
  pg_receivewal --create-slot --if-not-exists --slot "$SLOT" -d "dbname=replication" >/dev/null
}

timeline() {
  psql -d postgres -tAc "select timeline_id from pg_control_checkpoint()"
}

stream_wal() {
  # Runs for good; if the server goes away it is started again once the server is back.
  # One folder per timeline: pg_receivewal resumes from the newest file in its folder, and after
  # a restore (a new timeline) that position is on the old timeline, which the restored server
  # does not have, so the stream failed for good. A fresh folder makes it start from the slot,
  # which db-restore.sh creates as soon as the restored server is up.
  local dir
  while true; do
    wait_for_server
    ensure_slot || { sleep 1; continue; }
    dir="$WAL/tl$(timeline)" && mkdir -p "$dir" || { sleep 1; continue; }
    log "streaming WAL into $dir (slot $SLOT)"
    pg_receivewal -D "$dir" --slot "$SLOT" --synchronous --no-loop -d "dbname=replication" \
      || log "WAL stream stopped (exit $?); starting it again"
    sleep 0.2
  done
}

newest_base_age() {
  local newest
  newest=$(find "$BASE" -mindepth 1 -maxdepth 1 -type d -name '*Z' | sort | tail -n 1)
  [ -n "$newest" ] && [ -f "$newest/base.tar.zst" ] || { echo 999999999; return; }
  echo $(( $(date +%s) - $(stat -c %Y "$newest/base.tar.zst") ))
}

take_base_backup() {
  local name tmp
  name=$(date -u +%Y%m%dT%H%M%SZ)
  tmp="$BASE/.$name"
  log "base backup $name"
  rm -rf "$tmp"
  # -X none: the WAL comes from the stream above, which already holds every segment. zstd
  # unpacks about twice as fast as gzip, which is time the app is down during a restore.
  pg_basebackup -D "$tmp" -Ft --compress=client-zstd:3 -X none --checkpoint=fast -d "dbname=replication" --no-slot
  mv "$tmp" "$BASE/$name"
  rm -f /backups/.need-base
  printf 'LAST_BASE=%s\nLAST_BASE_BYTES=%s\n' "$name" "$(du -sb "$BASE/$name" | cut -f1)" \
    > /backups/status.env
  prune
}

prune() {
  local keep oldest label start
  mapfile -t keep < <(find "$BASE" -mindepth 1 -maxdepth 1 -type d -name '*Z' | sort | tail -n "$KEEP")
  find "$BASE" -mindepth 1 -maxdepth 1 -type d -name '*Z' | sort | head -n "-$KEEP" | xargs -r rm -rf
  oldest=${keep[0]:-}
  [ -n "$oldest" ] || return 0
  # The oldest kept base backup starts at a WAL segment; nothing before it is needed any more.
  label=$(tar --zstd -xOf "$oldest/base.tar.zst" backup_label 2>/dev/null || true)
  start=$(sed -n 's/^START WAL LOCATION: .* (file \([0-9A-F]\{24\}\))$/\1/p' <<<"$label")
  [ -n "$start" ] || return 0
  for dir in "$WAL"/tl*; do pg_archivecleanup "$dir" "$start"; done
}

wait_for_server
stream_wal &
STREAM=$!
trap 'kill "$STREAM" 2>/dev/null; exit 0' TERM INT

while true; do
  if [ -f /backups/.need-base ] || [ "$(newest_base_age)" -ge "$INTERVAL" ]; then
    wait_for_server
    take_base_backup || log "base backup failed; trying again in 60 s"
  fi
  sleep 60 &
  wait $! || true
done
