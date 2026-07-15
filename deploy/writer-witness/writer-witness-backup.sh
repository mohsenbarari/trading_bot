#!/usr/bin/env bash
set -Eeuo pipefail

BACKUP_DIR="${WRITER_WITNESS_BACKUP_DIR:-/var/backups/trading-bot-witness}"
RETENTION_DAYS="${WRITER_WITNESS_BACKUP_RETENTION_DAYS:-30}"
DATABASE_NAME="${WRITER_WITNESS_DATABASE_NAME:-writer_witness}"

install -d -m 0700 -o root -g root "$BACKUP_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$BACKUP_DIR/writer-witness-$timestamp.dump"

runuser -u postgres -- pg_dump \
    --format=custom \
    --compress=9 \
    --no-owner \
    --no-privileges \
    "$DATABASE_NAME" >"$target"
chmod 0600 "$target"
sha256sum "$target" >"$target.sha256"
chmod 0600 "$target.sha256"

find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'writer-witness-*.dump' -o -name 'writer-witness-*.dump.sha256' \
       -o -name 'writer-witness-*.dump.offsite.json' \) \
    -mtime "+$RETENTION_DAYS" -delete

echo "$target"
