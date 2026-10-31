#!/bin/sh
# restore_command for tools/db-restore.sh: copy WAL file $1 to $2 from the db-backup stream.
# Each timeline has its own folder (backup-loop.sh), and the newest segment of a timeline may
# still be .partial; a complete file wins over a partial one. WAL_DIR is for the tests.
WAL_DIR=${WAL_DIR:-/backups/wal}
for f in "$WAL_DIR"/tl*/"$1" "$WAL_DIR"/tl*/"$1.partial"; do
  [ -f "$f" ] && exec cp "$f" "$2"
done
exit 1
