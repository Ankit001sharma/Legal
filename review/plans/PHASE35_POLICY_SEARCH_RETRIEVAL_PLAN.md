# Phase 35 — Policy Search & Retrieval

**Status:** COMPLETE  
**Plan ID:** `DR-PHASE-35-POLICY-SEARCH-RETRIEVAL`  
**Priority:** P1 (after Phase 37C)  
**Scope:** `document_core` search/store, `review_agent` retrieval + preflight, prod env profiles, tests  
**Estimated diff:** ~200–280 LOC code + env/docs (no Java API changes)  
**Depends on:** Phase 37C (per-parent `metadata.categories`, **complete**), Phase 37D (chunk quality, **complete**), Phase 36 (ID-only scope, **complete**)  
**Related:** `PHASE35_RETRIEVAL_COMPLETENESS_PLAN.md` (discovery/zero-hit ops — merge T4/T7 into 35C)  
**Non-goals:** New embedding model, RRF/MMR rewrite, Java catalog sync, discovery cap redesign

---

## 1. Goal

**Contract section → correct policy section/chunk** for compare.

```
contract section
  → section_classifier (taxonomy-aligned categories)
  → multi_retrieval (dense + FTS + metadata)
  → rerank → category-aligned parent hit
  → compare
```

| Success | Metric |
|---------|--------|
| Scoped policy found | Every `policy_document_id` indexed before review |
| Category path works | Liability contract section retrieves liability-tagged **parent** |
| Prod semantic search | Hybrid + reranker on in production |
| Ops visibility | `retrieval_zero_hit_sections` alertable; general-only policies warned |

---

## 2. Progress today (~40%)

| Area | Done | Gap |
|------|------|-----|
| **3-path retrieval** | `multi_retrieval.py` dense + FTS + metadata + union + rerank | Metadata path uses **document-level** doc filter, not parent-category boost |
| **Category APIs** | `search_policy_by_categories`, `list_policy_ids_by_categories` | SQL scans `policy_documents.metadata.categories` only |
| **Parent categories at ingest** | 37C: per-parent `IndexedChunk.metadata.categories` | Search layer does not use them for filter/boost |
| **Compare alignment** | `compare_hit_selection.py` `category_aligned` mode | Runs **after** retrieval; wrong chunk may already be lost |
| **Hybrid search** | `pgvector_store._search_hybrid`, docker-compose `SEARCH_BACKEND=hybrid` | `document_core/config.py` default **`lexical`**; prod example incomplete |
| **Reranker** | `rerank_hits` wired in `multi_retrieval` | Prod profile does not enable cross-encoder explicitly |
| **Preflight** | LLM creds, MCP health, metadata probe | No **scoped policy indexed** check |
| **Artifact** | `retrieval_zero_hit_sections` in `section_retrieval_nodes` + artifact | No preflight warning for `general`-only policies; no ops alert doc |
| **Taxonomy** | `taxonomy.py` + `normalize_categories` in classifier | Classifier prompt **hardcoded** table — drift risk vs ingest tagger |

---

## 3. Root cause (why retrieval misses today)

```mermaid
flowchart LR
    subgraph ingest [Ingest 37C]
        P[Parent chunk categories per section]
        D[Document union categories]
    end
    subgraph search [Search today]
        L[list_document_ids_by_categories]
        S[search_policy_by_categories]
        M[multi_retrieval meta path]
    end
    C[Contract classifier categories]
    P --> D
    D --> L
    L --> S
    S --> M
    C --> M
```

| # | Root cause | Symptom | Fix (phase) |
|---|------------|---------|-------------|
| **RC1** | `list_document_ids_by_categories` reads **document row** only | Playbook with liability section but empty/stale doc `metadata.categories` → doc excluded | **35A.1** — OR match on **any parent chunk** |
| **RC2** | `search_policy_by_categories` searches all children in matched docs | Multi-topic playbook returns **indemnity parent** for liability query | **35A.2** — boost (or soft-filter) by **parent** `metadata.categories` |
| **RC3** | Metadata path scores same as lexical/dense; no parent-category signal in union | Wrong parent wins union max-score | **35A.3** — boost `meta_hits` + optional parent-category gate |
| **RC4** | Classifier prompt taxonomy ≠ `STANDARD_POLICY_CATEGORIES` | `indemnification` vs `indemnity` mismatch | **35A.4** — single source `taxonomy.py` |
| **RC5** | Prod deploy copies `config.py` default `search_backend=lexical` | Dense recall path weak | **35B.1** — prod `SEARCH_BACKEND=hybrid` |
| **RC6** | `retrieval_recall_top_k=20` default | Right child outside recall union | **35B.3** — prod 25–30 |
| **RC7** | Review starts before policies indexed | Zero hits, opaque failure | **35C.1** — preflight `index_status=indexed` |
| **RC8** | Policies tagged `general` only | Category filter + metadata path useless | **35C.2** — warn at preflight |

**37C unlocked RC1–RC3:** parent chunks now carry section categories; search layer was never updated.

---

## 4. Minimal-change strategy

| Do | Don't |
|----|-------|
| Extend **one SQL** in `list_document_ids_by_categories` | New search microservice |
| Add **`_boost_parent_category_hits()`** (~25 LOC) in `search.py`; reuse in `multi_retrieval` | Per-child category tags |
| Inject taxonomy into classifier prompt from `taxonomy.py` | Duplicate category lists |
| **Env-only** prod profile for hybrid/rerank/recall_k | Change CI to load embedding models |
| Extend **`run_review_preflight`** with scoped policy checks | New review graph node |
| One **E2E retrieval test** (NDA → liability chunk) | Full playbook corpus in CI |

**Single PR series:** `phase-35a-per-section-search` → `phase-35b-prod-profile` → `phase-35c-preflight-e2e` (or one PR if small team).

---

## 5. Architecture (target)

```mermaid
flowchart TD
    A[Contract section] --> B[classify_section_policies]
    B --> C[multi_retrieve_for_section]
    C --> D[dense: search_policy_recall hybrid]
    C --> E[fts: search_policy_fts]
    C --> F[meta: search_policy_by_categories]
    F --> G[list_document_ids_by_categories parent OR doc]
    F --> H[hybrid search in docs]
    H --> I[_boost_parent_category_hits]
    D --> J[union + rerank]
    E --> J
    I --> J
    J --> K[RetrievalHit parent with matching categories]
```

| Layer | Responsibility |
|-------|----------------|
| `document_chunks` parent `metadata.categories` | Source of truth per policy section (37C) |
| `policy_documents.metadata.categories` | Denormalized union (discovery fast-path) |
| `list_document_ids_by_categories` | Doc discovery: doc union **OR** any parent match |
| `search_policy_by_categories` | Doc filter → hybrid search → **parent category boost** |
| `multi_retrieval` meta path | Same boost before union; log `parent_category_boost_count` |
| `section_classifier` | Categories from shared `taxonomy.py` |
| Preflight | Scoped IDs exist + `indexed`; warn `general`-only |

---

## 6. Implementation order

```
Step 1  35A.4  Taxonomy alignment (classifier prompt) — no search dependency
Step 2  35A.1  list_document_ids_by_categories SQL (parent OR doc)
Step 3  35A.2  _boost_parent_category_hits in search.py
Step 4  35A.3  Wire boost in multi_retrieval meta path + search_policy_by_categories
Step 5  35B    Prod env profiles (hybrid, recall_k, reranker)
Step 6  35C.1  Preflight scoped policy indexed check
Step 7  35C.2  Preflight general-only warning
Step 8  35C.3  Zero-hit ops note + log warning in retrieval node
Step 9  Tests  unit + integration + E2E 35C.4
```

---

## 7. Task detail — 35A Per-section category search

### 35A.1 — `list_document_ids_by_categories` (parent OR document)

**File:** `document_core/store/pgvector_store.py` — `list_document_ids_by_categories` (~L552)

**Problem:** Query only checks `policy_documents.metadata->'categories'`. After 37C, authoritative tags live on **parent** rows; union should match but can lag (pre-37C data, manual DB edits, failed union).

**Change:** Add `OR EXISTS` on parent chunks (minimal, backward compatible):

```sql
SELECT DISTINCT pd.document_id
FROM policy_documents pd
WHERE pd.tenant_id = :tenant_id
  AND pd.kind = :kind
  AND pd.index_status = 'indexed'
  {stale_filter}
  AND (
    EXISTS (
      SELECT 1 FROM jsonb_array_elements_text(
        COALESCE(pd.metadata->'categories', '[]'::jsonb)
      ) cat WHERE cat = ANY(:categories)
    )
    OR EXISTS (
      SELECT 1 FROM document_chunks pc
      WHERE pc.tenant_id = pd.tenant_id
        AND pc.document_id = pd.document_id
        AND pc.chunk_role = 'parent'
        AND EXISTS (
          SELECT 1 FROM jsonb_array_elements_text(
            COALESCE(pc.metadata->'categories', '[]'::jsonb)
          ) c WHERE c = ANY(:categories)
        )
    )
  )
```

| LOC | ~20 SQL |
| Test | Extend `test_category_tagger.py` or new `test_list_policy_ids_by_parent_category.py`: ingest multi-section policy, clear doc `metadata.categories` in DB only → still listed via parent |

---

### 35A.2 — `search_policy_by_categories` parent boost

**File:** `document_core/services/search.py`

**New helper** (shared):

```python
def _parent_categories(hit: RetrievalHit) -> list[str]:
    raw = (hit.parent_chunk.metadata or {}).get("categories")
    return normalize_categories(raw if isinstance(raw, list) else [])

def boost_parent_category_hits(
    hits: list[RetrievalHit],
    categories: list[str],
    *,
    boost: float,
) -> list[RetrievalHit]:
    """Multiply score when parent section categories overlap query categories."""
    want = set(normalize_categories(categories))
    if not want or boost <= 0:
        return hits
    out: list[RetrievalHit] = []
    for hit in hits:
        score = hit.score
        if want.intersection(_parent_categories(hit)):
            score *= 1.0 + boost
        out.append(hit.model_copy(update={"score": score}))
    return sorted(out, key=lambda h: h.score, reverse=True)
```

**Wire** at end of `search_policy_by_categories`:

```python
hits = await search_document(request, store=store)
return boost_parent_category_hits(hits, categories, boost=settings.category_search_boost)
```

**Config** — `document_core/config.py`:

```python
category_search_boost: float = 0.15  # CHILD_CHUNK-style: match discovery_category_score_boost
```

Env: `CATEGORY_SEARCH_BOOST=0.15`

| LOC | ~35 |
| Test | `test_search_parent_category_boost.py`: two parents same doc (liability + indemnity), query liability → liability parent ranks first |

---

### 35A.3 — `multi_retrieval` metadata path

**File:** `review_agent/services/multi_retrieval.py`

**Change in `_retrieve_attempt`** after `meta_hits` returned (before normalize):

```python
from document_core.services.search import boost_parent_category_hits

meta_hits = await client.search_policy_by_categories(base, categories=categories)
# search_policy_by_categories already boosts if 35A.2 done — OR boost here only to avoid double:
# Prefer boost in document_core only (single place).
step["metadata_count"] = len(meta_hits)
```

If boost stays only in `search_policy_by_categories`, **35A.3** is:

1. Ensure `meta_path` passes normalized categories (already does).
2. Add retrieval meta: `parent_category_hits = sum(1 for h in meta_hits if overlap)` in `step`.
3. Optional **soft gate** in `multi_retrieval` (only if boost insufficient):

```python
def _prefer_category_parents(hits, categories, min_hits=1):
    want = set(normalize_categories(categories))
    aligned = [h for h in hits if want.intersection(_parent_categories(h))]
    return aligned if len(aligned) >= min_hits else hits
```

Apply to **union input** for meta path only (~10 LOC). Reuse `compare_hit_selection._hit_categories` — move to `document_core` or shared `category_utils.py` to avoid cross-import; **minimal:** duplicate 4-line read in `search.py` only.

**Review config** — reuse existing:

```python
discovery_category_score_boost: float = 0.15  # already in ReviewSettings
```

Alias env `RETRIEVAL_CATEGORY_SCORE_BOOST` → same field (optional, 2 LOC validator).

| LOC | ~15–25 |

---

### 35A.4 — Taxonomy alignment (classifier ↔ ingest tagger)

**Root cause:** `section_policy_classify.md` hardcodes category table; ingest uses `STANDARD_POLICY_CATEGORIES` + `taxonomy_prompt_labels()`.

**Files:**
- `review_agent/prompts/section_policy_classify.md` — replace static table with placeholder `{taxonomy_table}`
- `review_agent/services/section_classifier.py` — load prompt, inject generated table

**Generator** (add to `document_core/schemas/taxonomy.py` or `review_agent/services/taxonomy_prompt.py`):

```python
def taxonomy_classifier_table() -> str:
    """Markdown table rows for classifier prompt (sorted, excludes general)."""
    # Minimal: one row per category with short description from existing prompt dict
    ...
```

**Minimal v1:** Replace table body with:

```markdown
### Category taxonomy (use ONLY these lowercase tags)

Allowed: {taxonomy_labels}

Use `normalize_categories()` rules: indemnification → indemnity, etc.
```

Where `taxonomy_labels = taxonomy_prompt_labels()` from `document_core.schemas.taxonomy`.

| LOC | ~25 prompt + ~15 loader |
| Test | `test_section_classifier.py`: assert prompt contains `liability` and does not contain removed phantom tags |

---

## 8. Task detail — 35B Hybrid search & prod config

### 35B.1 — Prod `SEARCH_BACKEND=hybrid`

| File | Change |
|------|--------|
| `document_core/.env.production.example` | **NEW** — `SEARCH_BACKEND=hybrid` |
| `Legal ai/mcp/document_server/config.py` | Document default stays `lexical` for dev safety; prod via env |
| `document_core/config.py` | **No default change** (CI/dev lexical) |

### 35B.2 — Verify embeddings model in prod

| File | Setting |
|------|---------|
| `.env.production.example` | `EMBEDDING_MODEL=nomic-ai/modernbert-embed-base`, `EMBEDDING_DIM=768`, `EMBEDDING_ENABLED=true` |
| Ops note | document-mcp container must have model cache volume or warm-up job |

### 35B.3 — `retrieval_recall_top_k` 25–30

| File | Prod value |
|------|------------|
| `review_agent/.env.production.example` | `RETRIEVAL_RECALL_TOP_K=28` |
| `document_core/.env.production.example` | `RETRIEVAL_RECALL_TOP_K=28` (document-mcp serves search tools) |

Keep `document_core/config.py` default `20` for dev.

### 35B.4 — Cross-encoder reranker in prod

| File | Setting |
|------|---------|
| `document_core/.env.production.example` | `RERANKER_ENABLED=true`, `RERANKER_BACKEND=cross_encoder`, `RERANKER_MODEL=BAAI/bge-reranker-v2-m3` |
| `review_agent/.env.production.example` | Comment: rerank runs in document_core process when MCP in-process |

### 35B.5 — Document prod profile

**New file:** `document_core/.env.production.example`

```env
# Phase 35B — production retrieval profile
DOCUMENT_STORE_BACKEND=pgvector
DATABASE_URL=postgresql://legalai:legalai@postgres:5432/legalai
SEARCH_BACKEND=hybrid
SEARCH_HYBRID_ALPHA=0.5
EMBEDDING_MODEL=nomic-ai/modernbert-embed-base
EMBEDDING_DIM=768
EMBEDDING_ENABLED=true
RETRIEVAL_RECALL_TOP_K=28
RETRIEVAL_FINAL_TOP_K=10
RERANKER_ENABLED=true
RERANKER_BACKEND=cross_encoder
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
CATEGORY_SEARCH_BOOST=0.15
CHILD_CHUNK_MAX_CHARS=700
CHILD_CHUNK_OVERLAP_SENTENCES=2
CATEGORY_TAGGER_MODE=auto
```

**Extend:** `review_agent/.env.production.example` with cross-links + `RETRIEVAL_RECALL_TOP_K=28`.

### 35B.6 — CI stays lexical

| File | Guard |
|------|-------|
| `document_core/tests/conftest.py` | `os.environ.setdefault("SEARCH_BACKEND", "lexical")` |
| `review_agent/tests/conftest.py` | Same if integration loads hybrid |
| `.github/workflows/*` | Confirm no `SEARCH_BACKEND=hybrid` in CI env |

| LOC | ~40 env/docs, **0** runtime code if env-only |

---

## 9. Task detail — 35C Preflight & monitoring

### 35C.1 — Preflight: scoped policies exist + indexed

**File:** `review_agent/services/review_preflight.py`

**Extend** `run_review_preflight`:

```python
async def check_scoped_policies_indexed(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    policy_document_ids: list[str],
    contract_document_id: str | None = None,
) -> None:
    if contract_document_id:
        rec = await client.get_policy_registry_record(tenant_id, contract_document_id)
        # or get_contract — use existing registry API
        ...
    for pid in policy_document_ids:
        rec = await client.get_policy_registry_record(tenant_id, pid)
        if rec is None:
            raise ReviewPreflightError(f"policy document not found: {pid}")
        if rec.index_status != "indexed":
            raise ReviewPreflightError(
                f"policy {pid} index_status={rec.index_status}; expected indexed"
            )
```

**Wire** in `review_graph.run_review` — pass `tenant_id`, `policy_document_ids`, `contract_document_id` into preflight (already has IDs at L100+).

Use existing `DocumentMCPClient` registry methods (`list_policy_registry` / per-doc lookup).

| LOC | ~40 |
| Test | `test_review_preflight.py`: missing policy → `ReviewPreflightError`; pending → error |

---

### 35C.2 — Warn if policy has only `general` categories

**File:** `review_preflight.py` or `discovery_nodes.py` (after seed scope)

```python
def _warn_general_only_policies(records) -> list[str]:
    warnings = []
    for rec in records:
        cats = normalize_categories((rec.metadata or {}).get("categories"))
        if not cats or cats == ["general"]:
            warnings.append(f"policy {rec.document_id} has only general categories; retrieval may be weak")
    return warnings
```

Return warnings in preflight result → merge into review `warnings[]` (non-fatal).

| LOC | ~20 |

---

### 35C.3 — `retrieval_zero_hit_sections` alert

**Already implemented:** `section_retrieval_nodes.py` sets `compliance_stats.retrieval_zero_hit_sections`; `review_artifact.py` exports to `ops`.

**35C.3 deliverables (minimal):**

1. **Log warning** when `zero_hit_sections > 0` in `section_policy_retrieval_node` (1 line `logger.warning`).
2. **Ops runbook snippet** in this plan §12 (threshold: alert if `> 0` on prod reviews).
3. Phase 31 Prometheus (optional): counter `review_retrieval_zero_hit_total` — defer unless metrics enabled.

| LOC | ~5 code + docs |

---

### 35C.4 — E2E: NDA liability → liability-tagged chunk

**File:** `review_agent/tests/test_retrieval_category_e2e.py` (**new**)

**Flow (integration, Postgres + mocked LLM classify/compare):**

1. Ingest policy with `SAMPLE_POLICY` (liability + indemnity sections) — keyword tagger.
2. Ingest contract `SAMPLE_CONTRACT` (12.2 liability).
3. `run_review` with `policy_document_ids=[policy_id]`, `contract_document_id=...`.
4. Mock classifier to return `categories=["liability"]` for section 12.2 (or use lexical classify).
5. **Assert:**
   - `compliance_stats.retrieval_zero_hit_sections == 0`
   - `section_retrievals["12.2"].policy_hits[0].parent_chunk.metadata.categories` contains `liability`
   - Parent title/text mentions liability cap

| LOC | ~80 |
| Marker | `@pytest.mark.integration` |

---

## 10. File checklist

| File | Action |
|------|--------|
| `document_core/store/pgvector_store.py` | 35A.1 SQL |
| `document_core/services/search.py` | 35A.2 boost helper + wire |
| `document_core/config.py` | `category_search_boost` |
| `document_core/.env.production.example` | **NEW** 35B |
| `document_core/.env.example` | `CATEGORY_SEARCH_BOOST` |
| `document_core/tests/conftest.py` | 35B.6 `SEARCH_BACKEND=lexical` |
| `review_agent/services/multi_retrieval.py` | 35A.3 meta stats + optional soft gate |
| `review_agent/prompts/section_policy_classify.md` | 35A.4 taxonomy inject |
| `review_agent/services/section_classifier.py` | 35A.4 prompt loader |
| `review_agent/services/review_preflight.py` | 35C.1, 35C.2 |
| `review_agent/graph/review_graph.py` | Pass IDs to preflight |
| `review_agent/graph/section_retrieval_nodes.py` | 35C.3 log warning |
| `review_agent/.env.production.example` | 35B recall_k + comments |
| `review_agent/tests/test_review_preflight.py` | 35C.1 |
| `document_core/tests/test_search_parent_category_boost.py` | **NEW** 35A.2 |
| `review_agent/tests/test_retrieval_category_e2e.py` | **NEW** 35C.4 |

**Do not modify:** Java ingest API, `compare_hit_selection` logic (already category-aware), pgvector HNSW index DDL.

---

## 11. Acceptance criteria

| # | Criterion |
|---|-----------|
| AC1 | Policy with liability parent only in chunk metadata (doc metadata empty) appears in `list_document_ids_by_categories(tenant, ["liability"])` |
| AC2 | `search_policy_by_categories` returns liability parent above indemnity parent for same doc when query is liability-themed |
| AC3 | `multi_retrieval` metadata path logs `metadata_count > 0` for liability-classified contract section against tagged playbook |
| AC4 | Classifier prompt categories ⊆ `STANDARD_POLICY_CATEGORIES` (generated from `taxonomy.py`) |
| AC5 | Prod profile documents `SEARCH_BACKEND=hybrid`, `RETRIEVAL_RECALL_TOP_K≥25`, reranker on |
| AC6 | CI passes with `SEARCH_BACKEND=lexical` (no embedding model load) |
| AC7 | Preflight fails fast when `policy_document_id` missing or `index_status != indexed` |
| AC8 | Preflight warning (non-fatal) when scoped policy has only `general` categories |
| AC9 | NDA E2E: liability section `retrieval_zero_hit_sections == 0` and top hit parent has `liability` |
| AC10 | All unit tests pass; integration tests pass when Postgres available |

---

## 12. Ops runbook (35C.3)

| Artifact field | Threshold | Action |
|----------------|-----------|--------|
| `ops.retrieval_zero_hit_sections` | `> 0` | Check policy indexed, categories not `general`-only, `SEARCH_BACKEND=hybrid` in prod |
| `ops.classify_warning` count | high | Review classifier / taxonomy drift |
| `discovery.warnings` | `general only` | Re-ingest policy with 37C tagger LLM mode |

---

## 13. Risk & rollback

| Risk | Mitigation |
|------|------------|
| Parent SQL slower | `DISTINCT` + existing indexes on `(tenant_id, document_id)`; parent row count ≪ children |
| Double boost (search + multi) | Boost only in `document_core/services/search.py` |
| Hybrid OOM on MCP | Model cache volume; `EMBEDDING_ENABLED=false` rollback → lexical |
| Strict preflight blocks dev | `REVIEW_PREFLIGHT_ENABLED=false` or skip check when `index_status=pending` in dev only |
| Taxonomy prompt change shifts classify | Golden tests in `test_section_classifier.py` |

**Rollback:** `CATEGORY_SEARCH_BOOST=0`; revert SQL to doc-only; prod `SEARCH_BACKEND=lexical`.

---

## 14. Effort estimate

| Sub-phase | Hours | Notes |
|-----------|-------|-------|
| 35A | 6–8h | SQL + boost + taxonomy + tests |
| 35B | 2–3h | Env files only |
| 35C | 4–6h | Preflight + E2E |
| **Total** | **2.5–3.5 dev days** | vs 6–8 if including ops hardening + optional Prometheus |

Prior plan **~40% done** = 3-path + rerank + artifact field + hybrid code path exist; remaining **~60%** is per-parent category wiring + prod config + preflight.

---

## 15. Out of scope

| Item | Phase |
|------|-------|
| Per-child category filter in SQL | not planned |
| Discovery group cap changes | ops tuning only |
| `applies_to_contract_types` | removed (37A) |
| Java policy push | Phase 39 |

---

## 16. PR description template

```
Phase 35: policy search & retrieval (per-section categories)

35A: list_document_ids matches parent chunk categories; search boost by parent metadata.categories
35A: classifier prompt uses taxonomy.py
35B: .env.production.example hybrid + recall_k 28 + reranker
35C: preflight indexed scope; general-only warning; NDA liability E2E
```

---

## 17. Supersedes / merges

| Old task (`PHASE35_RETRIEVAL_COMPLETENESS_PLAN.md`) | New home |
|-----------------------------------------------------|----------|
| T4 Preflight scoped/indexed | **35C.1** |
| T5 Prod hybrid profile | **35B** |
| T7/T8 Zero-hit / E2E | **35C.4** |
| T1/T3 catalog_sync | **Removed** (Phase 36) |
| Discovery caps T6 | Ops doc only; explicit `policy_document_ids` avoids caps |

Keep old plan for discovery-miss narrative; **implement from this document** for search/retrieval work.
