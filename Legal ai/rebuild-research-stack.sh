#!/usr/bin/env bash
# Rebuild platform (research streaming) + frontend (UI perf), then restart.
set -eu

cd "$(dirname "$0")"

WSL_IP="$(hostname -I | awk '{print $1}')"
API_URL="http://${WSL_IP}:8081/api/v1"

echo "==> WSL IP: ${WSL_IP}"
echo "==> Rebuilding legal-ai-platform + frontend..."
export DOCKER_BUILDKIT=1
docker compose build legal-ai-platform frontend --build-arg "VITE_API_URL=${API_URL}"
docker compose up -d legal-ai-platform frontend

echo ""
echo "==> Done. Hard-refresh http://${WSL_IP}:3000 (Ctrl+Shift+R)"
