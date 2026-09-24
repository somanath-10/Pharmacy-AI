#!/usr/bin/env bash
# MongoDB backup — dumps the full database to a timestamped archive.
# Usage: ./scripts/backup-mongo.sh [output_dir]
# Cron:  0 2 * * * /opt/pharmaos/scripts/backup-mongo.sh /var/backups/pharmaos
set -euo pipefail

OUT_DIR="${1:-./backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
URI="${MONGODB_URI:-mongodb://localhost:27017}"
DB="${MONGODB_DB:-pharmacy_ai_os}"
RETAIN_DAYS="${RETAIN_DAYS:-14}"

mkdir -p "$OUT_DIR"
echo "→ dumping $DB to $OUT_DIR/$STAMP.archive.gz"
docker compose exec -T mongo mongodump --uri="$URI" --db="$DB" --gzip \
    --archive > "$OUT_DIR/$STAMP.archive.gz" 2>/dev/null \
  || mongodump --uri="$URI" --db="$DB" --gzip --archive \
       > "$OUT_DIR/$STAMP.archive.gz"

echo "→ uploaded documents (GridFS) live inside the same dump; file-storage "
echo "  uploads directory should be synced separately:"
if [ -d uploads ]; then
  tar czf "$OUT_DIR/$STAMP-uploads.tar.gz" uploads
fi

# retention
find "$OUT_DIR" -name "*.archive.gz" -mtime +"$RETAIN_DAYS" -delete 2>/dev/null || true
find "$OUT_DIR" -name "*-uploads.tar.gz" -mtime +"$RETAIN_DAYS" -delete 2>/dev/null || true
echo "✓ backup complete: $OUT_DIR/$STAMP.archive.gz"
