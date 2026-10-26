#!/usr/bin/env bash
# Logical PostgreSQL backup (metadata only - video files are never in the database).
#   scripts/backup-db.sh [backup-dir]      default: ./backups
set -euo pipefail
cd "$(dirname "$0")/.."
dir="${1:-backups}"
mkdir -p "$dir"
chmod 700 "$dir"
file="$dir/cctv-$(date +%Y%m%d-%H%M%S).dump"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner' > "$file"
chmod 600 "$file"
# Verify the archive is readable before declaring success.
docker compose exec -T db pg_restore --list < "$file" > /dev/null
echo "Backup written: $file ($(du -h "$file" | cut -f1))"
# Keep the 14 newest backups.
ls -1t "$dir"/cctv-*.dump 2>/dev/null | tail -n +15 | xargs -r rm --
