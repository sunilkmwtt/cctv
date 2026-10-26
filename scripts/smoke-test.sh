#!/usr/bin/env bash
# Post-deploy smoke test against the running compose stack. Read-only.
#   scripts/smoke-test.sh [base-url]      default: http://127.0.0.1:${CCTV_HTTP_PORT:-8080}
set -uo pipefail
cd "$(dirname "$0")/.."
port="$(grep -E '^CCTV_HTTP_PORT=' .env 2>/dev/null | cut -d= -f2)"
base="${1:-http://127.0.0.1:${port:-8080}}"
fail=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then echo "  ok   $1 ($3)"; else echo "  FAIL $1: expected $2, got $3"; fail=1; fi
}
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
published() { docker compose port "$1" "$2" 2>/dev/null | grep -v '^:0$' || true; }

echo "Containers:"
docker compose ps --format '  {{.Service}}: {{.State}} {{.Health}}'
for svc in db web indexer mediamtx proxy; do
  state="$(docker compose ps --format '{{.State}}' "$svc" 2>/dev/null)"
  check "service $svc running" running "${state:-missing}"
done
check "migrate exited cleanly" 0 "$(docker compose ps -a --format '{{.ExitCode}}' migrate 2>/dev/null || echo missing)"

echo "HTTP ($base):"
check "proxy health" 200 "$(code "$base/nginx-health")"
check "app health" 200 "$(code "$base/healthz/")"
check "login page" 200 "$(code "$base/login/")"
check "dashboard needs login" 302 "$(code "$base/")"
check "API needs login" 403 "$(code "$base/api/v1/cameras/")"
check "live HLS needs login" 401 "$(code "$base/stream/hls/entrance/index.m3u8")"
check "internal auth hidden" 404 "$(code "$base/internal/stream-auth/")"
check "protected files hidden" 404 "$(code "$base/protected/recordings/")"
check "static files served" 200 "$(code "$base/static/css/app.css")"

echo "Isolation:"
if docker compose exec -T web sh -c 'touch /app/recordings/.rw-test 2>/dev/null'; then
  check "recordings mount read-only" read-only writable; docker compose exec -T web rm -f /app/recordings/.rw-test
else
  check "recordings mount read-only" read-only read-only
fi
check "postgres not published" "" "$(published db 5432)"
check "MediaMTX API not published" "" "$(published mediamtx 9997)"
check "MediaMTX HLS not published" "" "$(published mediamtx 8888)"

[ "$fail" = 0 ] && echo "Smoke test passed." || { echo "Smoke test FAILED."; exit 1; }
