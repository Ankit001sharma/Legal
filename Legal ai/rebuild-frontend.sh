#!/usr/bin/env bash
# Rebuild frontend image with correct API URL + UUID fix, then restart container.
set -eu

cd "$(dirname "$0")"

WSL_IP="$(hostname -I | awk '{print $1}')"
API_URL="http://${WSL_IP}:8081/api/v1"

echo "==> WSL IP: ${WSL_IP}"
echo "==> VITE_API_URL: ${API_URL}"

export DOCKER_BUILDKIT=1
docker compose build frontend --build-arg "VITE_API_URL=${API_URL}"
docker compose up -d frontend

echo ""
echo "==> Frontend ready at:"
echo "    http://${WSL_IP}:3000"
echo "    http://localhost:3000  (if WSL port forwarding works)"
echo ""
echo "Hard-refresh the browser: Ctrl+Shift+R"
