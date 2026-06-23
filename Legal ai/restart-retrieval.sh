#!/usr/bin/env bash
# Restart retrieval + platform after docker-compose env changes.
set -eu

cd "$(dirname "$0")"

echo "==> Recreating retrieval-mcp and legal-ai-platform..."
docker compose up -d retrieval-mcp legal-ai-platform

echo ""
echo "==> Test search (expect JSON results, not 401):"
WSL_IP="$(hostname -I | awk '{print $1}')"
curl -s -X POST "http://${WSL_IP}:8002/tools/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"section 302 IPC India","search_type":"web","max_results":3}' \
  | head -c 400
echo ""
