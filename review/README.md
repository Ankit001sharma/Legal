# Contract Review (LangGraph library)

Review **logic** lives here. The **public API** is only:

```text
legal_ai_platform  →  POST /query  (task_type: review)
```

## Layout

```text
review/review_agent/
├── graph/           LangGraph pipeline
├── clients/         DocumentMCPClient (used in tests)
├── dimensions/      review_dimensions.yaml
├── state/
├── services/
└── reports/
```

Supporting packages (outside `review/`):

- `document_core/` — ingest, search, grounding library
- `Legal ai/mcp/document_server/` — MCP tools HTTP server

## Run via unified gateway

```bash
# Start document-mcp + platform (see legal_ai_platform/README.md)
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "review",
    "contract_text": "...",
    "policies": [{"title": "Policy", "text": "..."}]
  }'
```

## Memory (shared with research agent)

Review uses **retrieval-mcp** memory tools (same `MEMORY.md` store as research):

```text
load_memory   →  POST /tools/memory/search  (before review)
save_memory   →  POST /tools/memory/save    (after report)
```

Pass `thread_id` on `POST /query` to resume the LangGraph session checkpoint.

## LangGraph flow

```text
load_memory → index_policies → contract_parser → clause_detection
  → policy_retrieval → compliance_review → grounding → report → save_memory
```

## Development

Run graph tests without the platform:

```bash
cd review/review_agent
pip install -e ".[dev]" -e ../../document_core
pytest tests/
```

Do **not** run `review_agent.api.app` — that entry was removed in favour of one gateway.
