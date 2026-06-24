#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export DATABASE_URL="${DATABASE_URL:-postgresql://legalai:legalai@localhost:5435/legalai}"
export DOCUMENT_STORE_BACKEND=pgvector
export PYTHONPATH="../../document_core:.:../../Legal ai"
export GUARD_PASS_ENABLED=false
export FINAL_GAP_VERIFY_ENABLED=false
pytest tests/ -m integration -v "$@"
