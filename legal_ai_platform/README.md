# Legal AI Platform

Orchestration layer for the Legal AI system.

## Architecture

```
Client
  ↓
API Gateway (FastAPI)
  ↓
Query Orchestrator
  ↓
Agent Registry → Research Agent (and future agents)
  ↓
RetrievalMCPClient (HTTP)
  ↓
Legal ai Retrieval Server (/tools/*)
  ↓
External Sources
```

## Quick Start

1. Start the retrieval server (from `Legal ai/`):

   ```bash
   uvicorn mcp.retrieval_server.main:app --port 8001
   ```

2. Install and run the platform:

   ```bash
   cd legal_ai_platform
   pip install -e ".[dev]"
   cp .env.example .env
   legal-ai-gateway
   ```

   **HTTP/2 for remote Java clients** — enable TLS + HTTP/2 in `.env`:

   ```bash
   PLATFORM_HTTP2=true
   PLATFORM_SSL_CERTFILE=certs/dev-cert.pem
   PLATFORM_SSL_KEYFILE=certs/dev-key.pem
   ```

   Generate a self-signed certificate (include your server's LAN IP so other PCs can connect):

   ```bash
   python -m legal_ai_platform.scripts.generate_dev_cert --san IP:192.168.1.42
   legal-ai-gateway
   ```

   Clients must use `https://YOUR_SERVER_IP:8080`. Java example:

   ```java
   HttpClient client = HttpClient.newBuilder()
       .version(HttpClient.Version.HTTP_2)
       .sslContext(trustSelfSignedCert())  // dev only
       .build();
   ```

   For production, use a CA-signed certificate (e.g. Let's Encrypt) instead of the dev script.

   HTTP/1.1 fallback (no TLS): set `PLATFORM_HTTP2=false` or run
   `uvicorn legal_ai_platform.gateway.app:app --host 0.0.0.0 --port 8080`.

3. Submit a query (``session_id`` is created by your frontend and must be sent on every request):

   ```bash
   curl -X POST http://localhost:8080/query \
     -H "Content-Type: application/json" \
     -d '{"query": "What is the limitation period for breach of contract in India?", "session_id": "your-frontend-session-id"}'
   ```

## Multi-turn (clarification) sessions

The Research Agent may ask a clarifying question before researching. When the
response has `"awaiting_input": true`, send the next message with the **same**
`session_id` your frontend already owns:

```bash
# First call
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{"query": "I need help with a contract dispute", "session_id": "your-frontend-session-id"}'

# Follow-up — reuse the same session_id (the platform never generates one)
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d '{"query": "It is a SaaS vendor agreement governed by Indian law", "session_id": "your-frontend-session-id"}'
```

Sessions are held in an in-memory checkpointer, so they reset on restart. Swap
`MemorySaver` for a persistent checkpointer (e.g. Postgres) for durability.

> `AGENT_TIMEOUT_SECONDS` (default 300) bounds a single run; set `0` to disable.

## Adding a New Agent

1. Create `agents/<name>/<name>_agent.py` inheriting from `BaseAgent`.
2. Register it in `container.py`:

   ```python
   registry.register("contract", ContractAgent(...))
   ```

3. Add classification rules in `orchestration/classifier.py`.

No orchestrator code changes required.
