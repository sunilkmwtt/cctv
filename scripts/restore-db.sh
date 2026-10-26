#!/usr/bin/env bash
# Restore a pg_dump archive into the running db container. DESTRUCTIVE for the database
# metadata (not for videos). Stops web/indexer while restoring.
#   scripts/restore-db.sh backups/cctv-YYYYmmdd-HHMMSS.dump
set -euo pipefail
cd "$(dirname "$0")/.."
file="${1:?usage: $0 <backup.dump>}"
[ -f "$file" ] || { echo "No such file: $file" >&2; exit 1; }
read -r -p "Replace the current CCTV database with $file? [type yes] " answer
[ "$answer" = "yes" ] || { echo "Aborted."; exit 1; }
docker compose stop web indexer
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --single-transaction' < "$file"
docker compose up -d web indexer
echo "Restore complete."
