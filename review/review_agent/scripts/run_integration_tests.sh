#!/usr/bin/env bash
# Integration tests: prefer TEST_DATABASE_URL (legalai_test) over dev legalai DB.
set -euo pipefail
cd "$(dirname "$0")/.."
export TEST_DATABASE_URL="${TEST_DATABASE_URL:-postgresql://legalai:legalai@localhost:5435/legalai_test}"
export DATABASE_URL="${DATABASE_URL:-$TEST_DATABASE_URL}"
export DOCUMENT_STORE_BACKEND=pgvector
export PYTHONPATH="../../document_core:.:../../Legal ai"
export GUARD_PASS_ENABLED=false
export FINAL_GAP_VERIFY_ENABLED=false
pytest tests/ -m integration -v "$@"
