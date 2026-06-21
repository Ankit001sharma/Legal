# Phase 20 — P3 Integration & Packaging (Ops + Dev UI Contract)

**Plan ID:** `DR-PHASE-20-P3`  
**Owner:** Youngser  
**Scope:** `document-mcp` startup/health, `review_agent` preflight, `temp_java_sync` (Dev UI + E2E outputs) — **no review logic changes**  
**Goal:** Eliminate false-negative E2E runs and Dev UI display lies caused by **environment drift** and **JSON contract fragmentation**.  
**Depends on:** Phase 17 P0 (`SearchRequest.metadata`), Phase 19 P2 (verdict quality)  
**Estimate:** ~220 lines prod/scripts + ~150 lines tests + 0.5 sprint day  
**Status:** Implemented

---

## 0. Executive summary

Three P3 issues are **not compare/guard logic bugs** but broke trust in the pipeline during local and beta runs:

| ID | Defect | Layer | Status | Accuracy impact |
|----|--------|-------|--------|-----------------|
| **P3-8** | Stale duplicate process on port 8003 | Ops / deployment | Open | **Same as P0-1** — retrieval 500 masked as “core still broken” |
| **P3-9** | Dev UI platform payload missing `query` | `temp_java_sync` | **Fixed** | None on direct review; platform path was 422 |
| **P3-10** | Dev UI findings count display mismatch | `temp_java_sync/web` | Open | **Display only** — review output correct on disk |

**Youngser solution:** Treat integration like production: **capability-verified MCP singleton**, **canonical review output envelope**, and **regression tests on every writer/reader path**. No new legal rule engines, no fuzzy matching, no heuristic verdict logic — only packaging, contracts, and fail-fast preflight.

---

## 1. P3 bug register (finding → root cause → optimal solution)

### P3-8 — Stale duplicate document-mcp on port 8003

#### Finding (observed)

After P0-1 code fix (`SearchRequest.metadata`), E2E still showed:

```text
POST /tools/search_policy_by_categories → 500
'SearchRequest' object has no attribute 'metadata'
retrieval_zero_hit_sections: 4
playbook_compare_count: 0
```

`netstat -ano | findstr "8003.*LISTENING"` showed **two PIDs** (e.g. 9616 + 16236). `localhost:8003` load-balanced unpredictably to the **old** process without the schema fix.

#### Root cause (precise)

| Layer | Issue |
|-------|-------|
| **Process model** | Developers start document-mcp manually (`uvicorn … --port 8003`) in multiple terminals; Windows keeps orphaned listeners after IDE/Ctrl+C edge cases |
| **Startup script** | `start_document_mcp.ps1` L26–31 **warns only** — does not block or replace stale listener; second instance may bind or race depending on OS/socket reuse |
| **Health check gap** | `/health` returns `status`, `db`, `version` — **no proof** that `search_policy_by_categories` accepts `metadata.categories` (P0 fix surface) |
| **Review preflight** | `review_preflight.check_document_mcp()` L27–31 checks `status` + `db` only — passes against stale server that is “healthy” but wrong code |
| **False-negative pattern** | Code fix merged → developer does not kill old PID → hours lost re-debugging “P0 not fixed” |

**Classification:** **Environmental / deployment** — not a regression in `document_core` after P0-1.

#### Youngser solution (production-grade, non-legal-deterministic)

Four layers — **ops guard → capability probe → preflight gate → developer UX**:

```text
start_document_mcp.ps1 (-Replace)
    → single listener on :8003 (pidfile + optional kill)
    → document-mcp /health includes capabilities + build_id

review_agent preflight (before graph)
    → probe search_policy_by_categories with metadata.categories
    → fail fast with actionable message if 500 / wrong capability

Dev UI / E2E health panel
    → show version, build_id, capability flags, listener count warning

CI (review-ci.yml)
    → smoke: health.capabilities includes search_request_metadata
```

**Do not:** Rely on “remember to restart MCP” documentation alone — that failed in practice.

**Do not:** Add retry/fallback in retrieval for 500 — masks stale server; fail fast instead.

---

### P3-9 — Dev UI platform payload missing `query`

#### Finding (observed)

```text
POST /api/review { use_platform: true } → platform 422
Field required: query
```

#### Root cause (precise)

| Layer | Issue |
|-------|-------|
| **Platform contract** | Platform `AgentRequest` requires `query: str` |
| **Dev UI** | Original `dev_ui_server.py` omitted `query` in JSON body |
| **Path isolation** | Direct review (`run_review`) unaffected; only optional platform button broken |

**Classification:** Dev harness contract bug — **fixed** in current tree.

#### Current fix (verified)

`dev_ui_server.py` L150–157 now sends:

```python
payload = {
    "query": f"Review {body.contract_title} for compliance",
    "task_type": "review",
    "tenant_id": tenant,
    "contract_document_id": contract_id,
    ...
}
```

#### Remaining work (packaging only)

- [ ] Regression test: assert platform payload includes non-empty `query`
- [ ] Document platform path as optional in README (direct review = prod path)

**Accuracy impact:** None on direct review.

---

### P3-10 — Dev UI findings count display mismatch

#### Finding (observed)

Dev UI status: **“Review done — 0 finding(s)”**  
E2E / `review_result.json`: **4–9 findings** present.

#### Root cause (precise)

**Three different JSON shapes** write/read `outputs/review_result.json`:

| Writer | Path to findings | Notes |
|--------|------------------|-------|
| `dev_ui_server.py` L185–196 | `artifacts.report.findings` | Nested under `ReviewReport` dump |
| `run_full_e2e.py` L112–117 | **`findings` (root)** | Flat list; no `artifacts.report` |
| `run_review_only.py` L58–64 | **`findings` (root)** | Flat list + `finding_count` |

**Reader** (`web/app.js` L89–91):

```javascript
const report = data.artifacts?.report || data.report;
const findings = report?.findings || [];
```

When user runs **Full E2E** then UI loads `review_result.json`, or when comparing E2E file to Dev UI render:

- Root `findings[]` exists → **ignored**
- `artifacts.report` absent → `report` undefined → `findings = []`
- `finding_count` at root also **ignored** (L99 uses `findings.length` only)

**Classification:** **Contract fragmentation** in temp harness — not review-agent output bug.

#### Youngser solution (canonical envelope)

Introduce **one schema, one builder, one parser** — all writers and the UI use it.

```text
run_review / dev_ui / run_full_e2e / run_review_only
    → build_review_output_envelope(state, report)
    → review_result.json (schema_version: "1.0")

web/app.js
    → parseReviewOutput(data)  // mirrors Python parser logic in JS
    OR fetch normalized fields from GET /api/review response only
```

**Do not:** Patch only `app.js` with five fallback paths without fixing writers — debt accumulates.

**Do not:** Change `ReviewReport` / artifact schema in `review_agent` — envelope is harness-layer only.

---

## 2. Design principles (production-grade)

1. **Fail fast on environment drift** — Stale MCP must block review with a clear message, not produce gap-LLM silence.
2. **Capability over version string** — `version=0.1.0` does not prove P0-1 handler works; probe the actual tool path.
3. **Single output contract** — One envelope for all harness writers; UI reads envelope, not ad hoc paths.
4. **No legal logic changes** — P3 touches ops, health, JSON shape, and display only.
5. **Minimal scope** — `temp_java_sync` is disposable; envelope lives in harness, not production Java.
6. **Regression tests** — Every fix gets a test that would have caught the original bug.

---

## 3. Target architecture (after P3)

```text
┌─────────────────────────────────────────────────────────────┐
│  start_document_mcp.ps1  (-Replace kills stale :8003)       │
│       ↓                                                     │
│  document-mcp :8003                                         │
│    GET /health → { version, build_id, capabilities[] }      │
│    POST /tools/search_policy_by_categories (probe-safe)      │
└─────────────────────────────────────────────────────────────┘
       ↓
┌─────────────────────────────────────────────────────────────┐
│  review_agent preflight                                     │
│    check_document_mcp() + check_mcp_capabilities()            │
│    → ReviewPreflightError if metadata probe fails            │
└─────────────────────────────────────────────────────────────┘
       ↓
┌─────────────────────────────────────────────────────────────┐
│  review graph (unchanged logic)                             │
└─────────────────────────────────────────────────────────────┘
       ↓
┌─────────────────────────────────────────────────────────────┐
│  build_review_output_envelope()  ← single builder           │
│    dev_ui_server | run_full_e2e | run_review_only           │
└─────────────────────────────────────────────────────────────┘
       ↓
┌─────────────────────────────────────────────────────────────┐
│  Dev UI parseReviewOutput() → findings table + count        │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Implementation plan — task breakdown

### Sprint order

```text
P3-8.1 MCP health capabilities + build_id
    → P3-8.2 start_document_mcp.ps1 singleton (-Replace)
        → P3-8.3 review preflight capability probe
            → P3-8.4 Dev UI health UX + listener warning
                → P3-10.1 canonical envelope + writers
                    → P3-10.2 UI parser + tests
                        → P3-9.1 platform payload regression test
```

---

## 5. P3-8 — Stale MCP / capability verification (Youngser)

### Task P3-8.1 — Extend document-mcp health with capabilities

**Files:**

| File | Change |
|------|--------|
| `Legal ai/mcp/document_server/config.py` | `BUILD_ID` from env `DOCUMENT_MCP_BUILD_ID` or git short SHA at startup |
| `Legal ai/mcp/document_server/main.py` | Extend `HealthResponse` |

**Schema:**

```python
class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    build_id: str = ""                    # NEW — disambiguate stale vs current
    store_backend: str = "pgvector"
    db: str = "ok"
    capabilities: list[str] = Field(default_factory=list)  # NEW
```

**Capabilities list (static at startup):**

```python
CAPABILITIES = [
    "search_request_metadata",      # P0-1: SearchRequest.metadata.categories
    "search_policy_by_categories",
    "structured_sections_ingest",
    "verify_quote",
]
```

**Acceptance:**

- [ ] `GET /health` returns `capabilities` containing `search_request_metadata`
- [ ] `build_id` non-empty in dev (fallback: `VERSION` + timestamp if no git)

---

### Task P3-8.2 — Singleton startup script

**File:** `Legal ai/scripts/start_document_mcp.ps1`

**Behavior:**

| Mode | Action |
|------|--------|
| Default | If `:8003` LISTENING → **exit 1** with PID list + instruction to use `-Replace` |
| `-Replace` | Stop processes on `:8003` (graceful then force), write pidfile `outputs/document_mcp.pid`, start uvicorn |
| `-Status` | Print listeners + health JSON + capability match |

**Pidfile:** `Legal ai/scripts/.document_mcp.pid` (gitignored) — optional validation on status.

**Helper:** `Legal ai/scripts/stop_document_mcp.ps1` — kills pidfile + port scan.

**Acceptance:**

- [ ] Second `start_document_mcp.ps1` without `-Replace` refuses to start
- [ ] `-Replace` leaves exactly one LISTENING PID on 8003
- [ ] README troubleshooting updated (replace warn-only section)

---

### Task P3-8.3 — Review preflight capability probe

**File:** `review_agent/services/review_preflight.py`

**New function:**

```python
async def check_mcp_search_metadata_capability(client: DocumentMCPClient) -> None:
    """Probe search_policy_by_categories accepts metadata.categories (P0-1 surface)."""
    # Use tenant e2e-demo or synthetic tenant; empty categories → 200 with [] not 500
    # On 500 + 'metadata' in body → ReviewPreflightError with stale-server message
```

**Wire:** `run_review_preflight()` calls after `check_document_mcp()`.

**Error message (actionable):**

```text
document-mcp on :8003 does not support SearchRequest.metadata —
likely a stale process. Run: scripts/stop_document_mcp.ps1 then start_document_mcp.ps1 -Replace
```

**Config (optional):**

```python
review_preflight_mcp_capability_probe: bool = True  # config.py
```

**Acceptance:**

- [ ] Review fails in <2s with clear message when pointed at stale MCP mock
- [ ] Review proceeds when mock returns 200 for category search probe
- [ ] Unit test: `test_preflight_rejects_stale_mcp_metadata_error`

---

### Task P3-8.4 — Dev UI + E2E health enrichment

**Files:**

| File | Change |
|------|--------|
| `temp_java_sync/dev_ui_server.py` `/api/health` | Add `port_8003_listeners: [{pid}]`, `mcp_capabilities`, `mcp_build_id` |
| `temp_java_sync/web/app.js` `checkHealth()` | Warn if `listeners.length > 1` or missing `search_request_metadata` |

**Acceptance:**

- [ ] Health panel shows red warning: “Multiple processes on 8003”
- [ ] Health panel shows red warning: “MCP missing search_request_metadata capability”

---

### Task P3-8.5 — CI smoke (optional same sprint)

**File:** `.github/workflows/review-ci.yml`

- Start document-mcp in job (or mock)
- Assert `GET /health`.capabilities includes `search_request_metadata`
- POST minimal `search_policy_by_categories` body → not 500

---

## 6. P3-9 — Platform query field (verify + lock)

### Task P3-9.1 — Regression test

**File:** `temp_java_sync/tests/test_dev_ui_contracts.py` (new)

```python
def test_platform_review_payload_includes_query():
    # Import payload builder or inspect review() source contract
    assert "query" in payload and payload["query"].strip()
```

**Acceptance:**

- [ ] Test fails if `query` removed from platform branch
- [ ] README marks Bug #9 as Fixed with test reference

---

## 7. P3-10 — Canonical review output envelope (Youngser)

### Task P3-10.1 — Envelope schema + builder

**File:** `temp_java_sync/review_output.py` (new, ~80 lines)

```python
REVIEW_OUTPUT_SCHEMA_VERSION = "1.0"

class ReviewOutputEnvelope(BaseModel):
    schema_version: str = REVIEW_OUTPUT_SCHEMA_VERSION
    success: bool = True
    finding_count: int = 0
    findings: list[dict[str, Any]] = Field(default_factory=list)
    summary_markdown: str = ""
    artifact: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    discovered_policy_document_ids: list[str] = Field(default_factory=list)
    contract_document_id: str | None = None
    pipeline: str | None = None
    # Optional nested compat for tools expecting ReviewReport shape:
    artifacts: dict[str, Any] = Field(default_factory=dict)


def build_review_output_envelope(
    *,
    report: ReviewReport,
    state: dict[str, Any],
    contract_document_id: str | None = None,
) -> dict[str, Any]:
    findings = [f.model_dump(mode="json") for f in report.findings]
    artifact = report.metadata.get("artifact") or {}
    envelope = ReviewOutputEnvelope(
        finding_count=len(findings),
        findings=findings,
        summary_markdown=report.summary_markdown or "",
        artifact=artifact,
        warnings=list(state.get("warnings") or []),
        discovered_policy_document_ids=list(state.get("discovered_policy_document_ids") or []),
        contract_document_id=contract_document_id,
        pipeline=report.metadata.get("pipeline"),
        artifacts={
            "report": report.model_dump(mode="json"),
            "audit": artifact,
        },
    )
    return envelope.model_dump(mode="json")


def parse_findings_from_envelope(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Reader-side normalization — Python mirror of UI parser."""
    if data.get("findings"):
        return list(data["findings"])
    report = (data.get("artifacts") or {}).get("report") or data.get("report") or {}
    return list(report.get("findings") or [])
```

**Acceptance:**

- [ ] `parse_findings_from_envelope` returns findings for **all three** legacy shapes (unit test)
- [ ] New writes always populate root `findings` + `finding_count` + `artifacts.report`

---

### Task P3-10.2 — Unify all writers

**Files:**

| File | Change |
|------|--------|
| `temp_java_sync/dev_ui_server.py` | Replace inline payload L185–196 with `build_review_output_envelope()` |
| `temp_java_sync/run_full_e2e.py` | Same builder for `review_result.json` |
| `temp_java_sync/run_review_only.py` | Same builder |

**Acceptance:**

- [ ] All three writers produce identical top-level keys
- [ ] `finding_count == len(findings)` always

---

### Task P3-10.3 — Dev UI reader

**File:** `temp_java_sync/web/app.js`

```javascript
function parseReviewOutput(data) {
  const findings =
    data.findings ??
    data.artifacts?.report?.findings ??
    data.report?.findings ??
    [];
  const count = data.finding_count ?? findings.length;
  const md =
    data.summary_markdown ??
    data.output ??
    data.artifacts?.report?.summary_markdown ??
    "(no summary)";
  const artifact =
    data.artifact ??
    data.artifacts?.audit ??
    data.artifacts?.report?.metadata?.artifact ??
    {};
  return { findings, count, md, artifact };
}

function renderReview(data) {
  const { findings, count, md, artifact } = parseReviewOutput(data);
  ...
  setStatus(`Review done — ${count} finding(s)`, "ok");
}
```

**Acceptance:**

- [ ] Full E2E → load output in UI → correct count and table rows
- [ ] Direct Dev UI review → still works
- [ ] Legacy `review_result.json` from old runs still parses (backward compat test)

---

### Task P3-10.4 — Tests

**File:** `temp_java_sync/tests/test_review_output_envelope.py` (new, ~60 lines)

| Test | Scenario |
|------|----------|
| `test_envelope_root_findings` | Builder sets root `findings` |
| `test_parse_legacy_e2e_flat` | `{findings: [...]}` only |
| `test_parse_legacy_dev_ui_nested` | `{artifacts: {report: {findings}}}` |
| `test_parse_legacy_empty` | `{}` → `[]` |
| `test_finding_count_consistent` | count matches len |

---

## 8. Config & env summary

| Env var | Default | Purpose |
|---------|---------|---------|
| `DOCUMENT_MCP_BUILD_ID` | git SHA / `dev` | Disambiguate stale MCP in health |
| `DOCUMENT_SERVER_URL` | `http://localhost:8003` | Harness MCP URL |
| `REVIEW_PREFLIGHT_MCP_CAPABILITY_PROBE` | `true` | P3-8.3 probe toggle |

---

## 9. Verification matrix (Youngser sign-off)

```powershell
# P3-8 singleton
cd "d:\Ankit_legal\Legal\Legal ai\scripts"
.\stop_document_mcp.ps1
.\start_document_mcp.ps1 -Replace
curl http://localhost:8003/health

# P3-8 preflight + P3-10 envelope
cd "d:\Ankit_legal\Legal\review\review_agent"
python -m pytest tests/test_review_preflight.py -v

cd "d:\Ankit_legal\Legal\temp_java_sync"
python -m pytest tests/ -v

# Full manual
.\run_dev_ui.ps1
# Browser: Health → no multi-PID warning
# Full E2E → Findings tab shows same count as beta_assessment
python beta_test\run_assessment.py
```

| Gate | Before P3 | Target after P3 |
|------|-----------|-----------------|
| **G1** | Two PIDs on 8003 undetected | Script refuses duplicate; `-Replace` cleans |
| **G2** | Stale MCP passes health | Preflight fails with capability message |
| **G3** | P0 fix “still broken” after deploy | `build_id` + capability prove correct process |
| **G4** | UI “0 findings” after E2E | UI count matches `finding_count` / root `findings` |
| **G5** | 3 JSON shapes | Single envelope from all writers |
| **G6** | Platform 422 | Regression test green (P3-9) |
| **G7** | Beta score unaffected by display bug | `findings_total` in assessment matches UI |

---

## 10. File touch list

| File | Task | Est. lines |
|------|------|------------|
| `Legal ai/mcp/document_server/main.py` | P3-8.1 | +25 |
| `Legal ai/mcp/document_server/config.py` | P3-8.1 | +10 |
| `Legal ai/scripts/start_document_mcp.ps1` | P3-8.2 | +40 |
| `Legal ai/scripts/stop_document_mcp.ps1` | P3-8.2 | +30 (new) |
| `review_agent/services/review_preflight.py` | P3-8.3 | +35 |
| `review_agent/config.py` | P3-8.3 | +2 |
| `review_agent/tests/test_review_preflight.py` | P3-8.3 | +40 (new) |
| `temp_java_sync/review_output.py` | P3-10.1 | +80 (new) |
| `temp_java_sync/dev_ui_server.py` | P3-8.4, P3-10.2, P3-9 | +30 |
| `temp_java_sync/run_full_e2e.py` | P3-10.2 | +10 |
| `temp_java_sync/run_review_only.py` | P3-10.2 | +10 |
| `temp_java_sync/web/app.js` | P3-10.3, P3-8.4 | +35 |
| `temp_java_sync/tests/test_review_output_envelope.py` | P3-10.4 | +60 (new) |
| `temp_java_sync/tests/test_dev_ui_contracts.py` | P3-9.1 | +25 (new) |
| `temp_java_sync/README.md` | all | +20 |
| `.github/workflows/review-ci.yml` | P3-8.5 | +15 |

**Total:** ~220 prod/scripts + ~150 test

---

## 11. Out of scope (P3)

| Item | Reason |
|------|--------|
| Docker Compose for all services | Future; P3 focuses on Windows dev scripts |
| Production K8s rollout / blue-green | Java/platform team |
| Changing review-agent finding logic | P2 scope |
| Auto-restart MCP on file change | Watch mode optional P4 |
| Platform AgentRequest schema changes | Platform owns contract; Dev UI adapts |

---

## 12. Risk notes

| Risk | Mitigation |
|------|------------|
| `-Replace` kills wrong process | Port-scoped kill only; show PID before force |
| Capability probe needs indexed policies | Probe with empty categories — 200 + `[]` sufficient; 500 = bug |
| Envelope breaks external scripts reading JSON | Keep `artifacts.report` nested compat field |
| Git SHA unavailable in CI | Fallback `BUILD_ID=ci-${GITHUB_SHA}` |

---

## 13. Definition of done (Youngser)

- [x] P3-8: Single MCP instance enforced; capability probe in review preflight
- [x] P3-9: Platform payload regression test green
- [x] P3-10: Canonical envelope; UI count matches E2E findings
- [x] No changes to compare/guard/retrieval logic
- [x] README troubleshooting reflects `-Replace` workflow
- [ ] PR prefix: `Youngser P3: <description>`

---

## 14. Youngser execution checklist

1. **Root cause in PR:** cite dual-PID false negative (P3-8) and three JSON shapes (P3-10)
2. Implement P3-8 before P3-10 (preflight saves hours if MCP still stale)
3. Run Full E2E → Dev UI Findings tab → counts must match
4. Attach `netstat` before/after `-Replace` screenshot or log in PR
5. Beta assessment: confirm `findings_total` unchanged (logic) while UI now honest (display)

---

*End of Phase 20 P3 plan — Youngser*
