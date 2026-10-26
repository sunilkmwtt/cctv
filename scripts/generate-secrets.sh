#!/usr/bin/env bash
# Print fresh random values for .env. Nothing is written to disk.
set -euo pipefail
py() { docker run --rm python:3.12-slim-bookworm python -c "$1" 2>/dev/null || python3 -c "$1"; }
echo "DJANGO_SECRET_KEY=$(py 'import secrets; print(secrets.token_urlsafe(50))')"
echo "POSTGRES_PASSWORD=$(py 'import secrets; print(secrets.token_urlsafe(24))')"
echo "CCTV_FIELD_ENCRYPTION_KEY=$(py 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
