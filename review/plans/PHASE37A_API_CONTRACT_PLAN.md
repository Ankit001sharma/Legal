# Phase 37A — API Contract Cleanup & Remove `applies_to_contract_types`

**Status:** COMPLETE  
**Plan ID:** `DR-PHASE-37A-API-CONTRACT`  
**Priority:** P0  
**Scope:** Python only — `document_core`, `review_agent`, `legal_ai_platform`, fixtures, tests  
**Estimated diff:** ~350–500 LOC touched, ~150 LOC net deletion  
**Depends on:** Phase 36 (complete)  
**Non-goals:** DB column drop (deferred), per-parent LLM categories (Phase 37C), parser improvements (37B), Java code

---

## 1. Goal

Align runtime with production contract:

| Source | Sends |
|--------|--------|
| **Java** | `tenant_id`, `document_id`, `title`, `kind`, `text` (raw extracted string) |
| **Java does not send** | `categories`, `sections[]`, `applies_to_contract_types`, PDF bytes |
| **Python ingest** | Parse text → (later: tag categories) → chunk → embed |
| **Python review** | Scope = `policy_document_ids[]` only |

Remove **`applies_to_contract_types`** from all runtime logic. Policy scope is **never** filtered by contract type.

---

## 2. Minimal-change strategy

| Layer | Action |
|-------|--------|
| **Postgres column** `applies_to_contract_types` | **Keep** — always write `'{}'`; no migration in 37A |
| **`upsert_policy_registry` SQL** | Keep column in INSERT; pass empty array |
| **`IndexedChunk` / row hydration** | Stop reading into model (remove field) |
| **Search / discovery** | Delete filter branches only (~15 lines) |
| **Public ingest API** | Remove field from `IngestRequest` |
| **Fixtures** | Delete JSON key (bulk); no behavior change |
| **`categories` on ingest** | Ignore Java value for policies; keep `resolve_ingest_categories` (37C replaces later) |

**Do not** refactor `policy_discovery.py` beyond removing `applies_to` field plumbing (scoped path only in prod).

---

## 3. Java ingest contract (37A.1)

Document in this file + one paragraph in `document_core/README` or `review/README.md`.

### `POST /tools/index_policy`

```json
{
  "tenant_id": "acme",
  "document_id": "550e8400-e29b-41d4-a716-446655440000",
  "title": "Vendor Liability Policy",
  "text": "4. Limitation of Liability\n\nVendor liability shall not exceed..."
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `tenant_id` | yes | |
| `text` | yes | Raw string; newlines preserved; not PDF |
| `title` | yes | |
| `document_id` | recommended | Stable UUID for re-index |
| `kind` | no | Forced to `policy` by endpoint |
| `metadata` | no | e.g. `policy_ref` |
| `categories` | **ignored** | Assigned by Python at ingest |
| `applies_to_contract_types` | **removed** | Must not be sent |

### `POST /tools/ingest_document` (contracts)

Same shape; `kind` = `contract`. Optional `metadata.contract_type` for review routing (compare context), **not** for policy filtering.

### `POST /query` review (platform)

```json
{
  "task_type": "review",
  "contract_document_id": "...",
  "policy_document_ids": ["...", "..."]
}
```

---

## 4. Implementation order (strict)

Execute in this order to avoid half-broken builds:

```
Step 1  Search filters off          (pgvector_store, search.py)
Step 2  Ingest / chunk pipeline     (ingest, parent_child, content_hash)
Step 3  Schemas                     (chunk, registry, discovered_policy)
Step 4  Store protocol + registry   (protocol, pgvector_store, async_adapter, registry.py)
Step 5  Review agent cleanup        (nodes, policy_discovery, PolicyInput)
Step 6  Tests + fixtures
Step 7  Docs + grep gate
```

---

## 5. Task detail

### 37A.1 — Document Java contract

| Item | Action |
|------|--------|
| File | `review/plans/PHASE37A_API_CONTRACT_PLAN.md` (this file) §3 |
| File | Add 10-line “Java ingest” block to `review/README.md` ingest section |
| Acceptance | Java team can integrate from docs without reading Python |

---

### 37A.7 + 37A.8 — Remove search filters (do first)

**Why first:** Empty `applies_to` already means “no filter”; removing code paths cannot shrink recall.

#### `document_core/store/pgvector_store.py`

| Location | Change |
|----------|--------|
| `_chunk_from_row` / `_parent_from_row` (~L29–63) | Remove `applies_to_contract_types=` from `IndexedChunk(...)` |
| `list_document_ids_by_categories` (~L541–547) | Delete `contract_filter` block and `params["contract_type"]` for this query |
| `_search_lexical` loop (~L619–621) | Delete 3-line `contract_type not in applies` check |
| `_search_hybrid` / `_child_passes_filters` (~L713–715, ~729–730) | Delete same check (grep `applies_to_contract_types` in file) |

#### `document_core/services/search.py`

| Location | Change |
|----------|--------|
| `_child_matches_filters` (~L313–315) | Delete `applies_to_contract_types` branch; keep `kind` + `policy_type` only |

**Note:** `SearchRequest.contract_type` **stays** — used by review classifier/routing context, not policy exclusion after 37A.

**Tests:** Run `document_core/tests/test_ingest_search.py`, any search tests.

---

### 37A.5 + 37A.6 + 37A.14 — Ingest pipeline

#### `document_core/services/ingest.py`

```python
# Before build_parent_child_chunks — policies only (37A.14):
categories = []
if request.kind == DocumentKind.POLICY:
    section_texts = ...
    categories, extra_meta = resolve_ingest_categories(
        title=request.title,
        section_texts=section_texts,
        provided=None,  # ignore request.categories
        metadata=request.metadata,
    )
```

| Change | Detail |
|--------|--------|
| Remove | `applies_to_contract_types=request.applies_to_contract_types` from `build_parent_child_chunks` call |
| 37A.14 | Force `provided=None` in `resolve_ingest_categories` (ignore Java `categories: []`) |

#### `document_core/indexer/parent_child.py`

| Change | Detail |
|--------|--------|
| Remove param | `applies_to_contract_types` from `build_parent_child_chunks` signature |
| Remove | `applies = ...` and `applies_to_contract_types=applies` on `IndexedChunk` constructors |

#### `document_core/store/content_hash.py`

| Change | Detail |
|--------|--------|
| `_HASH_METADATA_KEYS` | Remove `"applies_to_contract_types"` |
| `metadata_fingerprint` | Delete `elif key == "applies_to_contract_types"` block |

#### `document_core/store/pgvector_store.py` — `save_document`

| Change | Detail |
|--------|--------|
| ~L107–115 | Remove `applies` variable; drop from `compute_content_hash` metadata dict |
| ~L167–178, ~L232–257 | Keep SQL column; bind `:applies_to` → `[]` always |
| `upsert_policy_registry` | Keep param **or** remove from signature (see 37A.4) — always `[]` |

---

### 37A.2 + 37A.3 — Schemas (`chunk.py`)

#### `IngestRequest`

- Delete field: `applies_to_contract_types: list[str]`
- Keep `categories` field for backward compat but **document as ignored** (removed in 37C from public contract if desired)
- No other changes

#### `IndexedChunk`

- Delete field: `applies_to_contract_types`

**Breaking:** Any code constructing `IndexedChunk(...)` must drop the kwarg (grep-driven fix).

---

### 37A.4 — Registry schemas & services

#### `document_core/schemas/registry.py`

| Model | Change |
|-------|--------|
| `RegisterPolicyRequest` | Remove `applies_to_contract_types` |
| `PolicyRegistryRecord` | Remove `applies_to_contract_types` (API response) |

#### `document_core/services/registry.py`

| Function | Change |
|----------|--------|
| `register_policy` | Stop passing `applies_to_contract_types` to upsert |
| `register_contract` | Stop `applies_to_contract_types=[request.contract_type]`; use `[]`; `contract_type` stays in `metadata` only |

#### `document_core/store/protocol.py` + `async_adapter.py`

**Minimal:** Remove `applies_to_contract_types: list[str]` from `upsert_policy_registry` signature; implementations pass `[]` to SQL internally.

---

### 37A.9 + 37A.10 — Review agent

#### `review_agent/schemas/discovered_policy.py`

- Remove `applies_to_contract_types` from `DiscoveredPolicy`

#### `review_agent/services/policy_discovery.py`

| Area | Change |
|------|--------|
| `seed_discovered_from_scope` (~L447) | Remove `applies_to_contract_types=` from `DiscoveredPolicy(...)` |
| `_merge_policy` / aggregation helpers (~L167–215, ~338, ~407, ~725) | Delete `applies_to` locals and dict keys |
| `discover_policies_from_topics` | Same — field removal only; **do not** refactor discovery algorithm |

#### `review_agent/graph/nodes.py` — `index_policies_node`

- Remove `"applies_to_contract_types"` key from `indexed_policies.append({...})` dict (~L98)

#### `legal_ai_platform/models/agent.py` — `PolicyInput`

- Remove `applies_to_contract_types` field (unused in review path)

---

### 37A.11 — Database (deferred)

| Item | 37A action |
|------|------------|
| `001_document_corpus.sql` column | **No change** |
| Runtime writes | Always `'{}'` |
| Future `002_drop_applies_to.sql` | Drop column from `policy_documents` + `document_chunks` when ops approves |

---

### 37A.12 — Fixtures

Remove `"applies_to_contract_types": [...]` line from JSON fixtures (key only):

```
temp_java_sync/fixtures/**/*.json          (~20 files)
temp_java_sync/fixtures/acme_nda/policies/*.json
```

#### `temp_java_sync/beta_test/scale_corpus.py`

| Change | Detail |
|--------|--------|
| `POLICY_LIBRARY` entries | Remove `applies_to_contract_types` key from each dict |
| `_policy_fixture` (~L507) | Remove `"applies_to_contract_types": raw[...]` line |

**Do not** change fixture `text` / `sections` content.

---

### 37A.13 — Tests

| File | Change |
|------|--------|
| `review_agent/tests/test_review_e2e.py` | Remove `applies_to_contract_types=` from `IngestRequest` |
| `review_agent/tests/test_policy_discovery.py` | Remove from `IngestRequest` / mock records (3 places) |
| `review_agent/scripts/load_test_reviews.py` | Remove from `index_policy` call |
| `legal_ai_platform/tests/test_review_gateway.py` | Remove from `IngestRequest` |
| `document_core/tests` | No current references — add none |

**Regression command:**

```bash
cd document_core && pytest tests/ -q
cd review/review_agent && pytest tests/ -q -m "not integration"
cd legal_ai_platform && pytest tests/test_review_gateway.py tests/test_orchestrator.py -q
```

---

### 37A.14 — Categories ignore (ingest)

| Behavior | Detail |
|----------|--------|
| Policies | Always run `resolve_ingest_categories` with `provided=None` |
| Contracts | No category tagging at ingest (unchanged) |
| Java sends `categories: []` | No effect — Python assigns |
| Java sends `categories: ["liability"]` | **Ignored in 37A** (prevents Java/Python drift) |

37C will replace keyword infer with per-parent LLM tagger; 37A only blocks Java input.

---

## 6. File checklist (grep-driven)

After implementation, runtime code must have **zero** matches:

```bash
rg "applies_to_contract_types" \
  document_core/document_core \
  review/review_agent/review_agent \
  legal_ai_platform/src \
  --glob "*.py"
```

Allowed remaining matches:

- `review/plans/*.md`
- `PRODUCTION_GRADE_REVIEW_AUDIT.md` (add “removed in 37A” note optional)
- `migrations/001_document_corpus.sql`
- `temp_java_sync` only if fixtures not yet cleaned (should be 0 after 37A.12)

---

## 7. Acceptance criteria

| # | Criterion |
|---|-----------|
| AC1 | `POST /tools/index_policy` with `{ tenant_id, title, text }` indexes successfully |
| AC2 | Extra JSON field `applies_to_contract_types` from old clients is **ignored** (field removed from schema — FastAPI drops unknown if not in model) |
| AC3 | Review with `policy_document_ids` retrieves policies regardless of `contract_type` |
| AC4 | `register_contract` + NDA ingest still sets `metadata.contract_type` for routing |
| AC5 | `rg applies_to_contract_types` clean on runtime `.py` under `document_core`, `review_agent`, `legal_ai_platform` |
| AC6 | Unit tests green (integration optional if no Postgres) |

---

## 8. Risk & rollback

| Risk | Mitigation |
|------|------------|
| Old indexed rows have non-empty `applies_to_contract_types` in DB | Filters removed in Step 1 — those values become inert |
| `PolicyRegistryRecord` API consumers read `applies_to_contract_types` | Field removed from response; always was optional |
| Content hash change (removed from fingerprint) | Text-only re-index on next ingest if only applies changed — acceptable |
| `scale_corpus` benchmarks | Strip key from corpus dicts |

**Rollback:** Revert single PR; DB column still exists with old data.

---

## 9. Effort estimate

| Step | Hours |
|------|-------|
| Search filter removal | 1–2h |
| Ingest + chunk + hash | 1–2h |
| Schemas + store + registry | 2–3h |
| Review agent + platform | 1–2h |
| Fixtures + tests | 2–3h |
| Docs + verification | 1h |
| **Total** | **1–2 dev days** |

---

## 10. Out of scope (next phases)

| Item | Phase |
|------|-------|
| Per-parent LLM category tagger | 37C |
| Remove `categories` from `IngestRequest` entirely | 37C |
| Drop DB column | Post-37A migration |
| Hybrid search prod profile | 35 |
| Java PDF extraction quality | Java / 39 |

---

## 11. PR structure (recommended)

Single PR `phase-37a-remove-applies-to-contract-types`:

1. `document_core` — filters, ingest, schemas, store
2. `review_agent` + `legal_ai_platform` — graph + models
3. fixtures + tests
4. docs

**~15–20 files**, focused diff, no drive-by refactors.
