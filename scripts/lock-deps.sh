#!/usr/bin/env bash
# Regenerate hash-pinned lock files inside the same Python image used in production.
set -euo pipefail
cd "$(dirname "$0")/.."
docker run --rm -v "$PWD/requirements:/req" -w /req python:3.12-slim-bookworm sh -c '
  pip install -q pip-tools==7.5.1 &&
  pip-compile -q --strip-extras --generate-hashes --allow-unsafe -o base.txt base.in &&
  pip-compile -q --strip-extras --generate-hashes --allow-unsafe -o dev.txt dev.in &&
  chown '"$(id -u):$(id -g)"' base.txt dev.txt'
