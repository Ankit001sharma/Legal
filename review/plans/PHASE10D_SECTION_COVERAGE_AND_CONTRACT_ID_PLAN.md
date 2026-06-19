# Phase 10D — Section Coverage (A3) + Contract-by-ID (B1/B2)

**Plan ID:** `DR-PHASE-10D`  
**Scope:** Review agent + platform gateway wiring only  
**Goal:** (A3) Every reviewable contract section appears in the final report; (B1/B2) Production reviews use pre-synced `contract_document_id` without re-ingest.  
**Depends on:** Phase 10 section-first pipeline, Phase 10C final gap verify  
**Estimate:** ~280 lines new/modify, ~120 lines tests, 1 sprint day  
**Out of scope:** Java sync worker, `sections[]` on ingest (Phase 8), tombstone API

---

## 0. Problem statement (verified in code)

### A3 — Silent uncovered sections

**Reviewable universe today:** sections in `contract_sections` with `len(text.strip()) >= review_min_section_chars` (default 40), via `filter_review_sections()` in `section_retrieval_nodes.py`.

**Gaps where a reviewable section can have NO finding in the report:**

| # | Cause | Code location |
|---|--------|----------------|
| G1 | Compare LLM returns items for some sections but **omits** others that had policy hits | `findings_for_no_policy_sections` skips when `bundle.policy_hits` **or** `section_id in compared_section_ids` — **wrong OR**: policy + no compare item → **no gap** | `section_merge.py` L77–78 |
| G2 | Compare batch **failed** for a section with policy | `_failure_items` may not cover every section in batch | `section_compare_llm.py` |
| G3 | Final gap verify **failed** (LLM error) for pure gap section | Placeholder may remain but not counted in ops; edge: superseded without replacement | `final_verify_llm.py` |
| G4 | Finding **dropped in grounding** (failed quote) | Section loses its only finding before `report_node` | `nodes.grounding_node` |
| G5 | No **explicit audit** that coverage = 100% | Report metadata has counts but no `uncovered_section_ids` | `report_node` |

**Acceptance (A3):** For every section in the reviewable set, the **final report `findings[]`** contains ≥1 row with matching `contract_section_id`, OR an explicit `INSUFFICIENT_POLICY_CONTEXT` / `INCONCLUSIVE` row with `metadata.gap_type` in `no_policy | coverage_backfill | compare_omitted`.

---

### B1/B2 — Re-ingest on every review

**Today:**

```text
run_review(contract_text=...)  →  contract_parser_node  →  ingest_document  →  new/duplicate index
                          →  clause_detection_node  →  list_sections
```

**Problems in production:**

- Duplicate chunks if same contract reviewed twice (unless same `document_id` — not passed today)
- Parser drift between review runs
- Large `contract_text` in every API call
- Java already synced contract to pgvector — Python ignores it

**Target:**

```text
run_review(contract_document_id=...)  →  contract_parser_node  →  list_sections ONLY
                                   →  clause_detection_node  →  skip if sections already loaded
```

---

## 1. Design principles

1. **Minimal graph change** — no new nodes; one small service + node hook before report.
2. **B before A in deploy order** — stable `contract_sections` from indexed doc makes coverage meaningful.
3. **Fail fast** — invalid/missing `contract_document_id` → clear error before LLM spend.
4. **Dev path preserved** — `contract_text` ingest still works for local tests.
5. **Single source of truth for reviewable sections** — reuse `filter_review_sections` + `section_review_sections` from state.

---

## 2. Part B — Contract-by-ID (B1 + B2)

### 2.1 API contract

**`run_review` signature change** (`review_graph.py`):

```python
async def run_review(
    *,
    client: DocumentMCPClient,
    tenant_id: str,
    contract_text: str = "",
    contract_document_id: str | None = None,  # NEW
    contract_title: str = "Contract",
    ...
) -> ReviewState:
```

**Validation rules:**

| Input | Rule |
|-------|------|
| `contract_document_id` set | `contract_text` optional (ignored for ingest; may still be used as fallback for routing if sections empty — should not happen) |
| Neither set | `raise ValueError("contract_text or contract_document_id required")` |
| Both set | **Prefer `contract_document_id`**; append warning: `"contract_text ignored when contract_document_id is set"` |
| Invalid UUID | `raise ValueError` before graph invoke |
| `list_sections` returns `[]` | `raise ValueError("contract document not found or not indexed: {id}")` |

**`ReviewState` addition** (`review_state.py`):

```python
contract_document_id: str | None
```

**Initial state** in `run_review`:

```python
"contract_document_id": contract_document_id,
"contract_text": contract_text or "",
```

---

### 2.2 B2 — `contract_parser_node` (`nodes.py`)

**Replace unconditional ingest with branch:**

```python
async def contract_parser_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    tenant_id = state["tenant_id"]
    doc_id_raw = state.get("contract_document_id")

    if doc_id_raw:
        document_id = UUID(str(doc_id_raw))
        sections = await client.list_sections(
            ListSectionsRequest(
                tenant_id=tenant_id,
                document_id=document_id,
                kind=DocumentKind.CONTRACT,
            )
        )
        if not sections:
            raise ValueError(f"contract document not indexed: {document_id}")

        title = (
            state.get("contract_title")
            or str(sections[0].metadata.get("document_title") or "").strip()
            or "Contract"
        )
        ingest_result = IngestResult(
            document_id=document_id,
            tenant_id=tenant_id,
            kind=DocumentKind.CONTRACT,
            title=title,
            parent_count=len(sections),
            child_count=0,
            structure_confidence=StructureConfidence.HIGH,
            warnings=["loaded existing contract by document_id; skipped re-ingest"],
        )
        return {
            "ingest_result": ingest_result,
            "contract_sections": sections,
            "warnings": list(ingest_result.warnings),
        }

    # Legacy dev path: inline text
    if not (state.get("contract_text") or "").strip():
        raise ValueError("contract_text required when contract_document_id is not set")

    request = IngestRequest(...)
    ingest_result = await client.ingest_document(request)
    ...
```

**Notes:**

- Sets **`contract_sections` early** so `clause_detection_node` can skip duplicate MCP call (see 2.3).
- `structure_confidence=HIGH` when loading indexed doc (already parsed at sync time).
- **No** `ingest_document` on ID path → no duplicate chunks.

---

### 2.3 B2 — `clause_detection_node` (3-line change)

```python
async def clause_detection_node(state: ReviewState, client: DocumentMCPClient) -> dict[str, Any]:
    existing = state.get("contract_sections")
    if existing:
        return {"contract_sections": existing}

    ingest = state["ingest_result"]
    sections = await client.list_sections(...)
    return {"contract_sections": sections}
```

---

### 2.4 B1 — Platform gateway (`review_agent.py`)

```python
contract_document_id = context.get("contract_document_id")
contract_text = (context.get("contract_text") or request.query or "").strip()

if not contract_document_id and not contract_text:
    return AgentResponse(success=False, error="contract_document_id or contract_text required")

result = await run_review(
    ...
    contract_text=contract_text,
    contract_document_id=str(contract_document_id) if contract_document_id else None,
    contract_title=context.get("contract_title", "Contract"),
    ...
)
```

**Java / platform payload (production):**

```json
{
  "task_type": "review",
  "tenant_id": "acme",
  "thread_id": "session-uuid",
  "context": {
    "contract_document_id": "550e8400-e29b-41d4-a716-446655440000",
    "contract_type": "msa"
  }
}
```

**Dev payload (unchanged):**

```json
{
  "context": {
    "contract_text": "...",
    "policies": []
  }
}
```

---

### 2.5 Java prerequisite (document once)

Java sync job (same as policy sync, `kind=contract`):

```http
POST document-mcp/tools/ingest_document
{
  "tenant_id": "acme",
  "document_id": "550e8400-...",
  "title": "Vendor MSA v3",
  "kind": "contract",
  "text": "... extracted text with headings ..."
}
```

Store returned `document_id` in Java `document_registry`; pass to review API.

**Optional env (prod guard — v1.1):**

```env
REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID=false   # set true in prod when Java ready
```

When `true`, `run_review` rejects `contract_text`-only calls.

---

### 2.6 B tasks checklist

| ID | Task | File | Lines |
|----|------|------|-------|
| B1.1 | Add `contract_document_id` to `ReviewState` | `review_state.py` | +1 |
| B1.2 | Extend `run_review` signature + validation + initial state | `review_graph.py` | +25 |
| B2.1 | Branch `contract_parser_node` (ID vs text) | `nodes.py` | +35 |
| B2.2 | Skip duplicate `list_sections` in `clause_detection_node` | `nodes.py` | +4 |
| B1.3 | Wire platform `ReviewAgent.execute` | `review_agent.py` | +12 |
| B1.4 | Update `JAVA_CATALOG_API_CONTRACT.md` review payload | plans doc | +15 |
| B1.5 | Tests: by-id load, missing doc error, text path unchanged | `test_contract_by_id.py` | +80 |

**B acceptance:**

- [ ] Review with `contract_document_id` never calls `ingest_document`
- [ ] `list_sections` called once (parser sets sections; clause_detection skips)
- [ ] `report.contract_document_id` matches input UUID
- [ ] Invalid UUID / empty sections → fail before compare LLM

---

## 3. Part A — Section coverage guarantee (A3)

### 3.1 Canonical reviewable set

**Definition (single function):**

```python
# review_agent/services/section_coverage.py  (NEW ~70 lines)

def reviewable_sections(
    contract_sections: list[IndexedChunk],
    *,
    min_chars: int,
) -> list[IndexedChunk]:
    return filter_review_sections(contract_sections, min_chars=min_chars)
```

**Use the same list as retrieval:** Prefer `state["section_review_sections"]` (serialized chunks from retrieval node) when present; else compute from `contract_sections`. Both should match if graph order unchanged.

---

### 3.2 Coverage audit function

```python
@dataclass
class SectionCoverageResult:
    findings: list[ComplianceFinding]
    warnings: list[str]
    uncovered_before: list[str]
    backfill_count: int

def ensure_section_coverage(
    reviewable: list[IndexedChunk],
    findings: list[ComplianceFinding],
    *,
    min_chars: int,
) -> SectionCoverageResult:
    """Append explicit gap findings for any reviewable section with no report row."""
```

**Algorithm:**

```text
reviewable_ids = {s.section_id for s in reviewable}
covered_ids = {f.contract_section_id for f in findings if f.contract_section_id}
uncovered = reviewable_ids - covered_ids

FOR each section_id in uncovered:
  append ComplianceFinding(
    status=INSUFFICIENT_POLICY_CONTEXT,
    severity=INFO,
    contract_section_id=section_id,
    dimension_label=f"Section {section_id} — review incomplete",
    rationale="No finding was produced for this section during compare, gap verify, or merge.",
    metadata={
      "gap_type": "coverage_backfill",
      "compliance_mode": "section_first",
      "review_min_section_chars": min_chars,
    },
  )
  warning: "coverage backfill added for section {id}"
```

**Do not** backfill sections **below** `min_chars` — they are intentionally out of scope.

---

### 3.3 Where to run coverage (graph hook)

**Option chosen (minimal):** extend **`report_node`** — coverage runs on findings **after grounding**, using `grounded_findings` as input and outputting augmented list into `ReviewReport`.

**Why after grounding:** Report only includes grounded findings today. Coverage backfill uses `INSUFFICIENT_POLICY_CONTEXT` (already `grounded=True` in grounding_node) so backfill rows survive grounding if added **before** grounding.

**Better hook (recommended):** new logic at end of **`final_gap_verify_node`** OR small function called from **`grounding_node` start**:

```text
final_gap_verify  →  findings (complete)
       ↓
ensure_section_coverage(findings)  →  append backfills  [NEW - in final_gap_verify_node return]
       ↓
grounding_node  →  grounded_findings
       ↓
report_node
```

**Implement in `final_gap_verify_node`** after merging new findings:

```python
from review_agent.services.section_coverage import ensure_section_coverage
from review_agent.services.section_filter import filter_review_sections

reviewable = [
    IndexedChunk.model_validate(s)
    for s in (state.get("section_review_sections") or [])
]
if not reviewable:
    reviewable = filter_review_sections(
        [IndexedChunk.model_validate(s) for s in (state.get("contract_sections") or [])],
        min_chars=settings.review_min_section_chars,
    )

coverage = ensure_section_coverage(reviewable, kept_findings + new_findings, ...)
return { "findings": coverage.findings, "warnings": warnings + coverage.warnings, ... }
```

Also store in state for report metadata:

```python
"section_coverage": {
  "reviewable_count": len(reviewable),
  "uncovered_before": coverage.uncovered_before,
  "backfill_count": coverage.backfill_count,
}
```

---

### 3.4 Fix root cause G1 (optional but recommended — 2 lines)

In `findings_for_no_policy_sections`, change skip condition:

```python
# Before (bug):
if bundle.policy_hits or section_id in compared_section_ids:
    continue

# After:
if section_id in compared_section_ids:
    continue
# If policy hits but no compare item → fall through to NO_POLICY gap
```

**Or** add separate branch for `compare_omitted`:

```python
if section_id in compared_section_ids:
    continue
if bundle.policy_hits:
    metadata={"gap_type": "compare_omitted", ...}
else:
    metadata={"gap_type": "no_policy", ...}
```

This reduces reliance on backfill alone; **keep `ensure_section_coverage` anyway** as safety net.

---

### 3.5 Report metadata (ops)

Add to `report_node` metadata:

```python
"section_coverage": state.get("section_coverage") or {},
"reviewable_section_count": ...,
"finding_section_ids": sorted({f.contract_section_id for f in findings if f.contract_section_id}),
```

**Hard acceptance check in `report_node` (prod):**

```python
if settings.enforce_section_coverage:  # default True
    reviewable_count = coverage.get("reviewable_count", 0)
    backfill = coverage.get("backfill_count", 0)
    if backfill > 0:
        report.warnings.append(f"{backfill} section(s) required coverage backfill")
    # assert len(finding section ids covering reviewable) == reviewable_count
```

---

### 3.6 A tasks checklist

| ID | Task | File | Lines |
|----|------|------|-------|
| A3.1 | New `section_coverage.py` with `ensure_section_coverage` | services | +70 |
| A3.2 | Call coverage at end of `final_gap_verify_node` | `section_compare_nodes.py` | +20 |
| A3.3 | Fix merge G1 (`compare_omitted` vs `no_policy`) | `section_merge.py` | +8 |
| A3.4 | Add `section_coverage` to state + report metadata | `review_state.py`, `nodes.py` | +15 |
| A3.5 | Config: `enforce_section_coverage: bool = True` | `config.py` | +1 |
| A3.6 | Tests: omitted compare, pure gap, full coverage | `test_section_coverage.py` | +90 |

**A acceptance:**

- [ ] Synthetic fixture: 3 reviewable sections, 1 compare item → backfill adds 2 findings
- [ ] After full pipeline mock, every reviewable `section_id` ∈ report findings
- [ ] `metadata.gap_type=coverage_backfill` on backfilled rows only
- [ ] Sections with `len(text) < min_chars` never get backfill
- [ ] Report metadata `section_coverage.backfill_count` accurate

---

## 4. Combined graph flow (after 10D)

```text
load_memory
  → contract_parser          # B2: ID → list_sections only | text → ingest
  → clause_detection         # skip if sections already set
  → [tenant_auto: routing → discovery]
  → index_policies
  → section_policy_retrieval # filter_review_sections
  → section_compare_llm
  → merge_section_findings   # A3.3 fix compare_omitted
  → final_gap_verify         # 10C + A3.2 coverage backfill
  → grounding
  → report                   # A3.5 metadata
  → save_memory
```

---

## 5. Test plan

### Unit (no Postgres)

| Test file | Cases |
|-----------|--------|
| `test_section_coverage.py` | empty findings → backfill all; partial coverage; below min_chars excluded |
| `test_section_merge.py` | policy hits + no compare item → gap with `compare_omitted` |
| `test_contract_by_id.py` | mock client: parser by-id no ingest; clause_detection skip |

### Integration (mock MCP)

| Test | Assert |
|------|--------|
| `test_review_by_document_id` | `ingest_document` not called; report has document_id |
| `test_review_coverage_e2e` | mock compare omitting section → backfill in final report |

### Manual prod smoke

```powershell
# 1. Ingest contract once via document-mcp
# 2. Review by ID only
POST /query { "context": { "contract_document_id": "..." }, "task_type": "review" }
# 3. Check report.metadata.section_coverage.backfill_count == 0 on happy path
```

---

## 6. Config & env

```env
REVIEW_MIN_SECTION_CHARS=40          # existing — coverage threshold
FINAL_GAP_VERIFY_ENABLED=true        # existing — reduces backfill need
ENFORCE_SECTION_COVERAGE=true        # NEW default true
REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID=false   # NEW prod gate (optional v1.1)
```

---

## 7. Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Backfill masks compare bugs | Log `coverage_backfill` count in report; alert if > 0 in prod |
| Wrong tenant/document_id | MCP `list_sections` scoped by `tenant_id` |
| Stale indexed contract | Java `content_hash` + re-sync; review reads latest index |
| Double list_sections on text path | Only ID path sets sections early; text path unchanged (2 calls today — acceptable) |

---

## 8. Implementation order

```text
Day 1 AM:  B2.1 → B2.2 → B1.2 → B1.3 → B tests
Day 1 PM:  A3.3 → A3.1 → A3.2 → A3.4 → A tests
Day 2:     Integration test + JAVA_CATALOG doc + prod env example
```

**Critical path:** B2 (stable sections from index) → A3.2 (coverage backfill at final verify).

---

## 9. Done definition (Phase 10D)

- [ ] `run_review(contract_document_id=...)` production path works end-to-end  
- [ ] No `ingest_document` on ID path  
- [ ] 100% reviewable section coverage in report findings  
- [ ] `compare_omitted` gaps fixed at merge + backfill safety net  
- [ ] Report exposes `section_coverage` stats  
- [ ] Unit tests green (Postgres optional for these tests — use mock MCP client)

---

*Minimal change: one new service file, branch in parser, coverage hook in existing final verify node, platform passthrough.*
