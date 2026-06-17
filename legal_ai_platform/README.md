# Legal AI Platform

**Single API gateway** for all agents (research, contract review, and future specialists).

## Architecture

```
Client (UI / curl / CLM)
        ↓
POST /query  — legal_ai_platform (:8080)   ← ONLY public entry
        ↓
Query Orchestrator + Task Classifier
        ↓
   +---------+---------+
   |                   |
ResearchAgent     ReviewAgent
   |                   |
retrieval-mcp     document-mcp
   |                   |
document_core (library, used by document-mcp)
```

There is **no separate** public `/review` service. Both agents use the same gateway.

## Quick start

1. Start MCP servers (from `Legal ai/`):

   ```bash
   docker compose up -d retrieval-mcp document-mcp
   ```

   Or locally:

   ```bash
   uvicorn mcp.retrieval_server.main:app --port 8001
   uvicorn mcp.document_server.main:app --port 8003
   ```

2. Install and run the platform:

   ```bash
   cd legal_ai_platform
   pip install -e ".[dev]"
   cp .env.example .env
   uvicorn legal_ai_platform.gateway.app:app --host 0.0.0.0 --port 8080
   ```

3. List registered agents:

   ```bash
   curl http://localhost:8080/agents
   ```

## Research (default)

```bash
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the limitation period for breach of contract in India?"}'
```

## Contract review (text MVP)

```bash
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{
    "task_type": "review",
    "tenant_id": "demo",
    "contract_title": "Vendor MSA",
    "contract_text": "12.2 Limitation of Liability. Liability shall not exceed fees paid in the prior twelve months.",
    "policies": [
      {
        "title": "Vendor Policy",
        "text": "4. Limitation of Liability. Liability cap is twelve months of fees."
      }
    ],
    "contract_type": "msa"
  }'
```

Response `artifacts.report` contains structured JSON; `output` is markdown.

Classifier also routes to review when `contract_text` + `policies` are present, or when the query matches review intent patterns.

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `RETRIEVAL_SERVER_URL` | `http://localhost:8001` | Research MCP |
| `DOCUMENT_SERVER_URL` | `http://localhost:8003` | Document MCP |
| `AGENT_TIMEOUT_SECONDS` | `300` | Max agent runtime |

## Adding a new agent

1. Implement `agents/<name>/<name>_agent.py` extending `BaseAgent`.
2. Register in `container.py`: `registry.register("task_type", agent)`.
3. Add classifier rules in `orchestration/classifier.py` if needed.

No orchestrator changes required.
