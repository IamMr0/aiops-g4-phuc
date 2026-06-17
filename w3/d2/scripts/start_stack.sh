#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== W3-D2 lightweight Docker stack ==="
docker compose up -d --build

echo "Waiting for api-gateway and AIOps pipeline..."
deadline=$((SECONDS + 120))
until curl -sf http://localhost:8080/checkout/health >/dev/null \
  && curl -sf "http://localhost:8000/alerts?since=0" >/dev/null; do
  if (( SECONDS > deadline )); then
    echo "Timed out waiting for stack readiness" >&2
    docker compose ps
    exit 1
  fi
  sleep 2
done

echo "stack ready"