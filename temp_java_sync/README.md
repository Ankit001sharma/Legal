# Temp Java Sync — E2E test harness

**Purpose:** Python-owned sync path via **normalization** (`normalization.sync` library or `:9001` HTTP). Registers and indexes contracts + playbooks via document-mcp, then runs **prod-style review** (`contract_document_id` + optional `policy_document_ids`).

Isolated tenants per benchmark (`e2e-demo`, `acme-nda-clean`, etc.).

---

## Prerequisites

1. **Postgres + pgvector** running
2. **document-mcp** on port 8003 (includes normalization tools in-process — **no `:9001` required** when `NORMALIZATION_MODE=mcp`)
3. **document-mcp** on port 8003 — **required** (normalization `:9001` removed in Phase 36)

```powershell
cd "d:\Ankit_legal\Legal\Legal ai\scripts"
.\start_postgres_podman.ps1
.\start_document_mcp.ps1 -Replace
```

Optional **legal_ai_platform** on `:8080` for “Review via platform” button only.

**Podman:** Postgres must be reachable at `127.0.0.1:5435` — start with `Legal ai\scripts\start_postgres_podman.ps1 -StartPodmanMachine` after `podman machine start`.

4. Copy env and set LLM key:

```powershell
cd "d:\Ankit_legal\Legal\temp_java_sync"
copy .env.example .env
# Edit .env → LLM_API_KEY=...
```

**Benchmark sync mode:** `BENCHMARK_SYNC_MODE=library` (default, in-process) or `http` (parity smoke via `NORMALIZATION_URL`).

---

## Dev UI (frontend for testing)

Browser UI at **http://localhost:8090** — sync calls **document-mcp** directly (`register` + `ingest_document` / `index_policy`).

```powershell
cd "d:\Ankit_legal\Legal\temp_java_sync"
.\run_dev_ui.ps1
```

**Buttons:**
1. **Sync** — normalization HTTP session/fixtures → document-mcp
2. **Run review** — direct review agent (prod path)
3. **Review via platform** — `POST /query` on `:8080` (optional)
4. **Tombstone smoke** — delete policy + verify search
5. **Full E2E** — all steps automated

**Prerequisites:** document-mcp (+ Postgres); LLM key for review; optional platform `:8080` for platform review button.

**Troubleshooting:** Dev UI `/api/health` → `document_mcp.db` must be `ok`.

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
  fixtures/             # sample NDA + policies (normalization payload shape)
  beta_test/            # benchmarks + normalization_sync.py
  run_full_e2e.py     # master script
  run_sync_only.py
  run_review_only.py
  run_e2e.ps1
  bootstrap_env.py
  .env.example
  outputs/            # results (created on run)
```
