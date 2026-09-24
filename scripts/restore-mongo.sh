#!/usr/bin/env bash
# Restore MongoDB from a backup archive created by backup-mongo.sh.
# Usage: ./scripts/restore-mongo.sh <archive-file>
# WARNING: drops the target database contents during restore.
set -euo pipefail

ARCHIVE="${1:?usage: restore-mongo.sh <archive-file>}"
URI="${MONGODB_URI:-mongodb://localhost:27017}"
DB="${MONGODB_DB:-pharmacy_ai_os}"

[ -f "$ARCHIVE" ] || { echo "archive not found: $ARCHIVE"; exit 1; }

read -r -p "Restore $ARCHIVE into $DB — this DROPS existing data. Type 'RESTORE' to continue: " ok
[ "$ok" = "RESTORE" ] || { echo "aborted"; exit 1; }

docker compose exec -T mongo mongorestore --uri="$URI" --drop --gzip \
    --archive < "$ARCHIVE" 2>/dev/null \
  || mongorestore --uri="$URI" --drop --gzip --archive < "$ARCHIVE"

echo "✓ restore complete. Restart API to rebuild indexes: docker compose restart api"
