# Temp Java Sync — E2E test harness

**Purpose:** Stand-in for the Java sync worker. Registers and indexes an NDA + playbooks via document-mcp (`sections[]`), then runs a **prod-style review** (`contract_document_id` only).

**Safe to delete** when real Java sync exists. Isolated tenant: `e2e-demo`.

---

## Prerequisites

1. **Postgres + pgvector** running
2. **document-mcp** on port 8003:

```powershell
cd "d:\Ankit_legal\Legal\Legal ai"
$env:DATABASE_URL="postgresql://postgres:YOUR_PASSWORD@localhost:5432/review"
$env:PYTHONPATH="d:\Ankit_legal\Legal\document_core;d:\Ankit_legal\Legal\Legal ai"
uvicorn mcp.document_server.main:app --host 0.0.0.0 --port 8003
```

3. Copy env and set LLM key:

```powershell
cd "d:\Ankit_legal\Legal\temp_java_sync"
copy .env.example .env
# Edit .env → LLM_API_KEY=...
```

---

## Dev UI (frontend for testing)

Browser UI at **http://localhost:8090** — no separate React build.

```powershell
cd "d:\Ankit_legal\Legal\temp_java_sync"
.\run_dev_ui.ps1
```

**Buttons:**
1. **Java sync** — register + structured ingest (NDA + 3 playbooks)
2. **Run review** — direct review agent (prod path)
3. **Review via platform** — `POST /query` on `:8080` (optional; requires `query` in payload)
4. **Tombstone smoke** — delete policy + verify search
5. **Full E2E** — all steps automated

**Tabs:** Summary markdown, findings table, audit artifact, raw JSON.

**Prerequisites:** document-mcp up; LLM key for review; platform optional for step 2b.

---

## Troubleshooting (Youngser P0)

### Port 8003 already in use / stale document-mcp

If review shows `retrieval_zero_hit_sections: 4`, `search_policy_by_categories` **500**, or preflight error **stale process**:

1. Check listeners: `netstat -ano | findstr "8003.*LISTENING"`
2. Stop **all** stale processes:

```powershell
cd "d:\Ankit_legal\Legal\Legal ai\scripts"
.\stop_document_mcp.ps1
```

3. Start **one** instance (refuses duplicate unless `-Replace`):

```powershell
.\start_postgres_podman.ps1
.\start_document_mcp.ps1 -Replace
```

4. Verify capability:

```powershell
.\start_document_mcp.ps1 -Status
# Must show: Capability OK: search_request_metadata
```

**Dev UI:** Health check warns if multiple PIDs on 8003 or capability missing.

### Missing langchain / review crashes

```powershell
cd "d:\Ankit_legal\Legal\review\review_agent"
.\scripts\install_deps.ps1
```

Or Dev UI auto-runs this when `import langchain` fails (`run_dev_ui.ps1`).

### Correct Postgres URL

Use **legalai-postgres on port 5435** (not `podman-vector-db` on 5432):

```text
DATABASE_URL=postgresql://legalai:legalai@localhost:5435/legalai
```

### Classifier fallback warnings

If review warnings contain `classifier fallback (categories=['general'])`, the section classifier LLM failed — retrieval may miss liability/indemnification playbooks. Fix deps first, then re-run sync + review.

### Beta assessment

```powershell
cd "d:\Ankit_legal\Legal\temp_java_sync"
python beta_test\run_assessment.py
```

Pass gates: `retrieval_zero_hit_sections: 0`, `playbook_compare_count >= 3`, score >= 7/10.

---

## Run CLI (next prompt / when ready)

```powershell
cd "d:\Ankit_legal\Legal\temp_java_sync"
.\run_e2e.ps1 -Mode full      # sync + review + tombstone
.\run_e2e.ps1 -Mode sync      # Java stub only
.\run_e2e.ps1 -Mode review    # review only (needs prior sync)

# Dev UI (browser testing)
.\run_dev_ui.ps1              # http://localhost:8090
```

Or:

```powershell
python run_full_e2e.py
```

---

## What it tests

| Step | Mimics |
|------|--------|
| `register_contract` + ingest `sections[]` | Java contract sync |
| `register_policy` + index `sections[]` + playbook metadata | Java playbook sync |
| `run_review(contract_document_id=...)` | Prod review path |
| `delete_policy` + search check | Tombstone (P2.3) |

---

## Outputs

Written to `outputs/` (gitignored):

- `sync_result.json` — document IDs, section IDs
- `review_result.json` — findings, artifact, summary
- `e2e_log.json` — step pass/fail log

---

## Fixtures

- `fixtures/nda_contract.json` — 4-section NDA
- `fixtures/policies/*.json` — confidentiality, liability, indemnification playbooks with `review_guidance` / `preferred_position`

---

## Layout

```text
temp_java_sync/
  web/                  # Dev UI (HTML + CSS + JS)
  dev_ui_server.py      # FastAPI :8090
  run_dev_ui.ps1
  fixtures/             # sample NDA + policies (Java payload shape)
  java_sync_stub/     # sync client (register + structured ingest)
  run_full_e2e.py     # master script
  run_sync_only.py
  run_review_only.py
  run_e2e.ps1
  bootstrap_env.py
  .env.example
  outputs/            # results (created on run)
```
