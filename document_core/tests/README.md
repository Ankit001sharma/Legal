# PostgreSQL tests for document_core

## Test database (separate from MCP dev)

Use a dedicated database so pytest `TRUNCATE` does not conflict with a running document-mcp on `legalai`:

```powershell
createdb legalai_test
# or: psql -c "CREATE DATABASE legalai_test;"
set TEST_DATABASE_URL=postgresql://legalai:legalai@localhost:5435/legalai_test
```

MCP dev server should use `DATABASE_URL=.../legalai`.

## Running tests

```powershell
# Unit-only (no Postgres): reranker and other non-store tests
cd Legal\document_core
python -m pytest -m "not integration" -q

# Integration (requires Postgres)
python -m pytest -m integration -q
```

All tests using the `store` fixture are auto-marked `integration`.
