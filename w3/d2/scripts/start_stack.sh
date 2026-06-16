#!/usr/bin/env bash
set -e
echo "=== start_stack.sh ==="
echo "Starting 10-service stack..."

docker compose up -d

echo "Waiting for AIOps pipeline /alerts endpoint to respond 200..."
timeout 120 bash -c 'until curl -sf http://localhost:8000/alerts?since=0 >/dev/null; do sleep 2; done'

echo "stack ready"
