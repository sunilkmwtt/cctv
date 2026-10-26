#!/usr/bin/env bash
# Read-only checks of the host before/after deploying. Changes nothing.
set -uo pipefail
rec="${1:-/home/sunil/cctv/recordings}"
echo "== host";      hostnamectl --static 2>/dev/null; nproc; free -h | head -2; timedatectl | grep 'Time zone'
echo "== disk";      df -h "$rec" | tail -1
echo "== recorder";  systemctl is-active cctv.service; systemctl is-enabled cctv.service
echo "== owner";     stat -c '%U uid=%u gid=%g %A %n' "$rec"; echo "use APP_UID=$(stat -c %u "$rec") APP_GID=$(stat -c %g "$rec")"
echo "== layout";    find "$rec" -maxdepth 3 -not -path '*/.parts/*' | sort | tail -25
echo "== in-progress parts"; find "$rec" -path '*/.parts/*' -name '*.mkv' -mmin -5 | tail -5
echo "== docker";    docker --version 2>/dev/null || echo "docker NOT installed"; docker compose version 2>/dev/null
echo "== routes (check no overlap with 10.89.1.0/24, 10.89.2.0/24)"; ip -4 route | grep -v '^default' | awk '{print "  " $1 " " $3}'
