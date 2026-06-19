# Sprint 2 — Structured Ingest + Contract Registry + Tombstone (P2.1–P2.3)

**Plan ID:** `DR-PHASE-16-P2`  
**Scope:** `document_core` + document-mcp (+ thin client mirrors)  
**Goal:** Java sends **stable section boundaries**; Python stores them as-is; heuristic parser remains **dev fallback**; deleted policies never retrieved.  
**Depends on:** Phase 4 pgvector, P1 contract-by-ID review path  
**Estimate:** ~320 lines prod code, ~220 lines tests, **3–4 days**  
**Explicitly excluded:** Review graph changes, PDF/DOCX parsers, Java sync worker, rule engine, PLAYBOOK_LOAD_REGISTRY wiring

---

## 0. Problem (verified in code today)

### Two ingest paths

| Path | Code | Section source | Confidence |
|------|------|----------------|------------|
| **Prod** | `contract_parser_node` → `list_sections` when `contract_document_id` set (`nodes.py` L33–66) | Whatever was indexed at sync time | `HIGH` if indexer was good |
| **Dev / inline** | `ingest_document` → `parse_text_to_tree` (`ingest.py` L22–26, `text_parser.py`) | **Regex heuristic** on raw text | Often `MEDIUM`/`LOW` |

Heuristic patterns: `12.2 Title`, `Section 4`, `ARTICLE II` — breaks on messy PDF extracts, merged clauses, non-standard numbering.

### What already exists (reuse, don’t rebuild)

| Asset | Location |
|-------|----------|
| `policy_documents.kind IN ('contract','policy')` | `migrations/001_document_corpus.sql` L7 |
| `index_status` pending/indexed/failed | `migrations/002_policy_registry_status.sql` |
| `register_policy` + stable UUID | `services/registry.py` L17–50 |
| `list_documents` filters `index_status='indexed'` | `pgvector_store.py` L298–302 |
| Content-hash idempotent re-index | `save_document` L109–136 |
| `build_parent_child_chunks(tree)` | `indexer/parent_child.py` — **unchanged** |
| Review by ID skips re-ingest | P1 / `PHASE10D` — **no graph change needed** |

### Gaps (P2 fixes)

| Gap | Risk |
|-----|------|
| No `sections[]` on `IngestRequest` | Java must send raw text → heuristic at sync |
| No `register_contract` | Contracts lack registry row before index |
| No tombstone | `list_document_ids_by_categories` **does not** filter `index_status` (`pgvector_store.py` L500–509) — stale policies can still be retrieved |
| `index_status` has no `deleted` | No explicit lifecycle for retired playbooks |

---

## 1. Design principles

1. **Structured first, heuristic fallback** — if `sections[]` present → skip `text_parser`; else today’s path unchanged.  
2. **One index pipeline** — both paths produce `DocumentTree` → `build_parent_child_chunks` → `save_document`.  
3. **Registry before index** — `register_*` creates metadata row (`pending`); full ingest sets `indexed`.  
4. **Soft tombstone** — set `index_status=deleted`; filter in all ID resolution paths; **keep chunks** for audit (optional hard delete later).  
5. **Minimal surface** — no review_agent graph edits; MCP + schemas + ingest only.  
6. **Flat sections v1** — Java sends list of top-level clauses; nested `children[]` deferred to P2.1b.

---

## 2. Target flow (after P2)

```text
Java sync (prod)
  register_contract / register_policy     → policy_documents row (pending)
  index_* with sections[]                 → store sections as-is (HIGH confidence)
  review(contract_document_id)            → list_sections (unchanged)

Dev / test
  ingest_document(text only)            → heuristic parser (fallback)

Policy retired
  delete_policy(policy_ref)             → index_status=deleted → excluded from search
```

---

## 3. Java / API contract (dynamic data — not Python constants)

### 3.1 Structured ingest payload

```json
{
  "tenant_id": "acme",
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "Vendor MSA v3",
  "kind": "contract",
  "contract_type": "msa",
  "metadata": {
    "contract_ref": "acme-vendor-msa-2026",
    "source": "java-sync"
  },
  "sections": [
    {
      "section_id": "12.2",
      "title": "Limitation of Liability",
      "text": "The total liability of either party shall not exceed..."
    },
    {
      "section_id": "8.1",
      "title": "Indemnification",
      "text": "Vendor shall indemnify Customer..."
    }
  ]
}
```

**Rules for Java:**

- `section_id` — stable ID used in findings (`contract_section_id`); must be unique within document.  
- `text` — full clause body (heading may be repeated in text or not; store as sent).  
- `sections[]` non-empty → **`text` field optional** on request (canonical built from sections).  
- Omit `sections[]` → must send `text` (heuristic fallback).

Policy ingest identical with `kind: "policy"` + optional `categories[]`.

### 3.2 Register contract

```json
{
  "tenant_id": "acme",
  "contract_ref": "acme-vendor-msa-2026",
  "title": "Vendor MSA v3",
  "document_id": "550e8400-...",
  "metadata": { "contract_type": "msa", "parties": ["Acme", "Vendor Co"] }
}
```

### 3.3 Tombstone

```json
{
  "tenant_id": "acme",
  "policy_ref": "vendor-indemnity-standard"
}
```

---

## 4. Task breakdown

### P2.1 — `sections[]` structured ingest

#### 4.1.1 Schema (`document_core/schemas/chunk.py`)

Add (~25 lines):

```python
class IngestSectionInput(BaseModel):
    section_id: str = Field(..., min_length=1)
    title: str = ""
    text: str = Field(..., min_length=1)
    level: int = Field(default=1, ge=0, le=6)

class IngestRequest(BaseModel):
    ...
    text: str = Field(default="", description="Raw text; required if sections empty")
    sections: list[IngestSectionInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_text_or_sections(self) -> Self:
        if not self.sections and not (self.text or "").strip():
            raise ValueError("text or sections[] required")
        return self
```

Remove strict `min_length=1` from `text` field; validate in model validator instead.

#### 4.1.2 Tree builder (`document_core/parser/structured_sections.py`) — NEW ~45 lines

```python
def sections_to_tree(
    *,
    document_id: UUID,
    title: str,
    sections: list[IngestSectionInput],
) -> DocumentTree:
    """Flat sections → DocumentTree with structure_confidence=HIGH."""
```

- Each input → `SectionNode(level=section.level or 1, children=[])`  
- `canonical_text` = `\n\n`.join(f"{s.section_id} {s.title}\n{s.text}" for s in sections)  
- Dedupe `section_id` — raise `ValueError` on duplicate (fail fast for Java bugs)

#### 4.1.3 Ingest service (`document_core/services/ingest.py`) — modify ~20 lines

```python
async def ingest_document(request: IngestRequest, *, store=None) -> IngestResult:
    if request.sections:
        tree = sections_to_tree(
            document_id=document_id,
            title=request.title,
            sections=request.sections,
        )
        warnings.append("structured sections ingest; heuristic parser skipped")
    else:
        tree = parse_text_to_tree(document_id=document_id, title=request.title, text=request.text)
    parents, children = build_parent_child_chunks(tree=tree, ...)
    ...
```

**No changes** to `build_parent_child_chunks` or `save_document`.

#### 4.1.4 MCP

No new tool — existing `/tools/ingest_document` and `/tools/index_policy` accept extended `IngestRequest` automatically via Pydantic.

#### 4.1.5 Review agent

**Zero graph changes.** Prod flow already:

```text
contract_document_id → list_sections → section-first pipeline
```

Optional doc-only: warn in `contract_parser_node` if `structure_confidence != high` on ID path (already warns on inline ingest).

#### 4.1.6 Acceptance (P2.1)

- [ ] Ingest with `sections[]` → `structure_confidence=high`, N parent chunks with exact `section_id`/text.  
- [ ] Ingest with `text` only → same behavior as today (heuristic).  
- [ ] Duplicate `section_id` in `sections[]` → 400/ValueError.  
- [ ] `list_sections` returns structured sections; review E2E finds correct `contract_section_id`.  
- [ ] Content-hash skip still works for unchanged structured re-sync.

---

### P2.2 — `register_contract`

#### 4.2.1 Schema (`document_core/schemas/registry.py`) — add ~15 lines

```python
class RegisterContractRequest(BaseModel):
    tenant_id: str
    contract_ref: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    document_id: UUID | None = None
    contract_type: str | None = None
    source: str = "catalog"
    metadata: dict[str, Any] = Field(default_factory=dict)
```

Reuse `PolicyRegistryRecord` — already has `kind: Literal["contract","policy"]`.

#### 4.2.2 Service (`document_core/services/registry.py`) — add ~25 lines

```python
def stable_contract_document_id(tenant_id, contract_ref, provided=None) -> UUID:
    return uuid5(NAMESPACE_DNS, f"{tenant_id}:contract:{contract_ref}")

def register_contract(request: RegisterContractRequest, *, store=None) -> PolicyRegistryRecord:
    doc_store = store or get_store()
    document_id = stable_contract_document_id(...)
    meta = {**request.metadata, "contract_ref": request.contract_ref}
    if request.contract_type:
        meta["contract_type"] = request.contract_type
    return doc_store.upsert_policy_registry(
        tenant_id=...,
        document_id=document_id,
        policy_ref=request.contract_ref,  # DB column is generic ref
        title=request.title,
        kind="contract",
        policy_type=request.contract_type,
        applies_to_contract_types=[request.contract_type] if request.contract_type else [],
        source=request.source,
        metadata=meta,
        index_status="pending",
    )
```

Optional (~10 lines):

```python
def get_contract_by_ref(tenant_id, contract_ref, *, store=None) -> PolicyRegistryRecord | None:
    return get_policy_by_ref(tenant_id, contract_ref)  # same lookup; verify kind=contract
```

#### 4.2.3 MCP (`document_server/main.py`) — add ~12 lines

```python
@app.post("/tools/register_contract", response_model=PolicyRegistryRecord)
async def register_contract_tool(request: RegisterContractRequest) -> PolicyRegistryRecord:
    return register_contract(request)

@app.post("/tools/get_contract_by_ref", response_model=PolicyRegistryRecord)
async def get_contract_by_ref_tool(request: GetPolicyByRefRequest) -> PolicyRegistryRecord:
    record = get_policy_by_ref(request.tenant_id, request.policy_ref)
    if record is None or record.kind != "contract":
        raise HTTPException(404, "contract not found")
    return record
```

Reuse `GetPolicyByRefRequest` — field name `policy_ref` holds `contract_ref` (document in plan; Java uses same shape).

#### 4.2.4 Client (`review_agent/clients/document_client.py`) — add ~8 lines

Mirror `register_policy` / `get_policy_by_ref` for contract variants (platform/Java may call MCP directly).

#### 4.2.5 Acceptance (P2.2)

- [ ] `register_contract` → row in `policy_documents` with `kind=contract`, `index_status=pending`.  
- [ ] Same `contract_ref` → stable `document_id` (uuid5).  
- [ ] After `ingest_document(sections[])` → `index_status=indexed`.  
- [ ] `get_contract_by_ref` returns record; wrong kind → 404.

---

### P2.3 — Tombstone `delete_policy`

#### 4.3.1 Migration (`document_core/migrations/005_tombstone_status.sql`) — ~8 lines

```sql
ALTER TABLE policy_documents
  DROP CONSTRAINT IF EXISTS policy_documents_index_status_check;

ALTER TABLE policy_documents
  ADD CONSTRAINT policy_documents_index_status_check
  CHECK (index_status IN ('pending', 'indexed', 'failed', 'deleted'));
```

No chunk deletion in v1 — soft tombstone only.

#### 4.3.2 Schema

```python
class DeletePolicyRequest(BaseModel):
    tenant_id: str
    policy_ref: str = Field(..., min_length=1)

class DeletePolicyResult(BaseModel):
    tenant_id: str
    policy_ref: str
    document_id: UUID
    index_status: Literal["deleted"]
```

Optional: `DeleteContractRequest` — or single `delete_document_ref` with kind check. **Minimal:** `delete_policy` only (policies are tombstone target); contracts use same helper internally if needed later.

#### 4.3.3 Store (`pgvector_store.py`) — add ~35 lines

```python
def tombstone_policy_by_ref(self, tenant_id: str, policy_ref: str) -> PolicyRegistryRecord | None:
    """SET index_status='deleted' WHERE tenant_id AND policy_ref; return row or None."""
```

Update **`list_document_ids_by_categories`** — add:

```sql
AND index_status = 'indexed'
```

Update **`list_policy_registry`** default — exclude `deleted` unless `index_status` filter explicitly requested.

**Already correct:** `list_documents` (L301), `_resolve_document_ids` → uses `list_documents`.

**Edge case:** `list_sections` on deleted doc — return **404** at MCP layer if registry status is `deleted` (optional guard in `list_sections` service ~8 lines).

#### 4.3.4 Service (`document_core/services/registry.py`)

```python
def delete_policy(request: DeletePolicyRequest, *, store=None) -> DeletePolicyResult:
    record = doc_store.tombstone_policy_by_ref(...)
    if record is None:
        raise ValueError(f"policy not found: {request.policy_ref}")
    return DeletePolicyResult(...)
```

#### 4.3.5 MCP

```python
@app.post("/tools/delete_policy", response_model=DeletePolicyResult)
async def delete_policy_tool(request: DeletePolicyRequest) -> DeletePolicyResult:
    ...
```

#### 4.3.6 Discovery safety (`review_agent/services/policy_discovery.py`)

**Optional 5 lines:** when resolving discovered docs, skip any ID whose registry `index_status != indexed` (belt-and-suspenders if search filter missed).

#### 4.3.7 Acceptance (P2.3)

- [ ] After `delete_policy`, `search_policy` returns no hits from that doc.  
- [ ] `list_document_ids_by_categories` excludes deleted.  
- [ ] `list_policy_registry` default omits deleted.  
- [ ] Re-`index_policy` same ref + new content → status back to `indexed` (existing upsert in `save_document`).  
- [ ] Chunks remain in DB (soft tombstone) — verify with SQL in test.

---

## 5. File change matrix

| File | Action | Task | ~Lines |
|------|--------|------|--------|
| `schemas/chunk.py` | Modify | P2.1 `IngestSectionInput`, validator | 30 |
| `parser/structured_sections.py` | **Create** | P2.1 tree builder | 45 |
| `services/ingest.py` | Modify | P2.1 branch | 20 |
| `schemas/registry.py` | Modify | P2.2/P2.3 request models | 25 |
| `services/registry.py` | Modify | register/delete contract/policy | 55 |
| `store/protocol.py` | Modify | tombstone method, status literal | 10 |
| `store/pgvector_store.py` | Modify | tombstone + category filter | 40 |
| `migrations/005_tombstone_status.sql` | **Create** | P2.3 | 8 |
| `services/search.py` | Modify | list_sections deleted guard | 10 |
| `document_server/main.py` | Modify | 3 new tools | 35 |
| `review_agent/clients/document_client.py` | Modify | client mirrors | 20 |
| `tests/test_structured_ingest.py` | **Create** | P2.1 | 90 |
| `tests/test_register_contract.py` | **Create** | P2.2 | 50 |
| `tests/test_delete_policy.py` | **Create** | P2.3 | 60 |
| `tests/test_ingest_search.py` | Modify | regression heuristic | 15 |
| `.env.example` (document_core if any) | Modify | docs | 5 |

**Total:** ~520 lines (incl. tests). **Zero** review graph nodes.

---

## 6. Implementation order

```text
Day 1 — P2.1 core
  IngestSectionInput + sections_to_tree + ingest branch
  test_structured_ingest.py (unit + pg integration)

Day 2 — P2.2
  register_contract + MCP + client
  test_register_contract.py

Day 3 — P2.3
  migration 005 + tombstone + category filter fix
  delete_policy MCP + tests

Day 4 — Integration smoke
  Java-style payload: register_contract → ingest sections[] → review by document_id
  delete_policy → confirm discovery/search empty
```

---

## 7. Test plan

| Layer | Tests |
|-------|--------|
| Unit | `sections_to_tree` dedupe, canonical text, HIGH confidence |
| Unit | `IngestRequest` validator — text OR sections |
| Integration | Structured ingest → `list_sections` count + section_id match |
| Integration | Heuristic ingest unchanged (`test_ingest_search` regression) |
| Integration | register_contract → pending → ingest → indexed |
| Integration | delete_policy → search returns [] for that doc |
| Integration | Category filter excludes deleted (`test_multi_retrieval` extend) |
| E2E | `test_review_e2e` with pre-indexed structured contract (optional new fixture) |

Mark Postgres tests `@pytest.mark.integration` (same pattern as P6).

---

## 8. Definition of done (Sprint 2)

1. Java can send **`sections[]`**; Python stores boundaries **without heuristic**.  
2. **`text`-only ingest** still works for dev/tests.  
3. **`register_contract`** mirrors policy registry for contracts.  
4. **`delete_policy`** tombstones; retrieval/discovery **never returns** deleted playbooks.  
5. Review pipeline **unchanged** — benefits automatically via better indexed sections.  
6. All new tests pass; existing unit suite green.

---

## 9. Explicit non-goals (Sprint 2)

- PDF/DOCX layout parsers  
- Nested `sections[].children[]` (P2.1b later)  
- Hard delete of chunks (GDPR job — later)  
- `delete_contract` (add if Java needs; same tombstone helper)  
- Java sync worker implementation  
- Review agent graph / compare / guard changes  
- PLAYBOOK_LOAD_REGISTRY wiring (P4 optional)

---

## 10. Production notes

### Idempotent Java sync

```text
1. register_contract(policy)     → pending row, stable document_id
2. ingest with sections[]        → skip if content_hash unchanged (existing)
3. review(contract_document_id)  → never re-ingest per review (P1)
```

### Observability

Return in `IngestResult.warnings`:

- `"structured sections ingest; heuristic parser skipped"`  
- `"structure_confidence=low"` — only on heuristic path  

Include `structure_confidence` in registry `metadata` after index (optional, 3 lines in `save_document`).

### Rollout

| Env | Behavior |
|-----|----------|
| Dev | `text` paste still works |
| Staging | Java sends `sections[]` on sync |
| Prod | `REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID=true` + structured sync only |

---

## 11. Quick reference — before vs after

| Question | Before P2 | After P2 |
|----------|-----------|----------|
| Where do sections come from (prod)? | Heuristic at sync unless Java pre-indexed elsewhere | Java `sections[]` explicit |
| Wrong clause boundaries? | Common on messy extracts | Rare — Java owns structure |
| Stale deleted policy? | Can still appear in category filter | `deleted` excluded |
| Contract registry? | Only implicit on ingest | `register_contract` + stable ID |
| Review code changes? | — | None |

---

**Summary:** Sprint 2 = **one structured ingest branch** + **contract registry parity** + **tombstone fix** in document_core/MCP. Heuristic stays for dev. Review pipeline (P1–P6) consumes better sections automatically through existing `list_sections` + `contract_document_id` path.
