# Phase 35 — Policy Retrieval Completeness (No Miss)

**Status:** PLANNED — superseded for implementation by `PHASE35_POLICY_SEARCH_RETRIEVAL_PLAN.md` (35A/B/C)  
**Plan ID:** `DR-PHASE-35-RETRIEVAL-COMPLETE`  
**Priority:** P1 (production retrieval quality)  
**Scope:** Python — `document_core`, `review_agent`, document-mcp config  
**Estimated diff:** ~120 LOC code + env/docs (no Java/frontend required for core fixes)  
**Depends on:** Phase 26 (metadata at ingest), Phase 28 (staleness), Phase 32 (integration tests), **Phase 36** (ingest simplification)  
**Non-goals:** New search algorithm, RRF/MMR, discovery rewrite, Java catalog service changes

> **Phase 36 note:** Tasks **T1** (catalog_sync category merge) and **T3** (catalog reindex) are **removed** when `catalog_sync.py` is deleted. Java push-ingest via `/tools/index_policy` owns categories and reindex. See `PHASE36_INGEST_PATH_SIMPLIFICATION_PLAN.md`.

---

## 1. Goal

Minimize **missed policies** and **zero-hit sections** in production review.

Two distinct problems (do not conflate):

| Problem | Question | Success metric |
|---------|----------|----------------|
| **P1 — Discovery miss** | Which **policy documents** enter the review? | Expected policy IDs ⊆ `discovered_policy_document_ids` |
| **P2 — Section miss** | Which **chunks** does each section retrieve? | `retrieval_zero_hit_sections == 0` in artifact |

**Truth:** The system is designed for **relevant** policies under caps, not “every policy in tenant every time.” For **guaranteed full set**, use explicit scope (§8).

---

## 2. Architecture (what runs today)

```
Contract → routing (topics) → policy_discovery → index_policies
         → per section: classify → multi_retrieval (3 paths) → compare
```

### Search APIs (section retrieval)

| API | Path | Role |
|-----|------|------|
| `search_policy_recall` | Dense/recall | `multi_retrieval.py` L207 |
| `search_policy_fts` | FTS | L212 |
| `search_policy_by_categories` | Metadata | L220 |
| `list_policy_ids_by_categories` | Doc ID filter | `multi_retrieval._resolve_filter_document_ids` |

### Discovery (document selection)

| Mechanism | Code | Notes |
|-----------|------|-------|
| Topic search | `search_policy` | `_search_topics` L319 |
| Category sweep | `search_policy_by_categories` | `_discover_by_section_categories` L370+ |
| Explicit scope | `seed_discovered_from_scope` | **No group cap** when `scope_document_ids` set |
| Group + cap | `_group_and_cap` | Default **6 groups** adaptive → 20 |

---

## 3. Root causes → solutions (verified)

### Layer A — Data & index (biggest ROI)

| ID | Root cause | Evidence | Symptom | Solution |
|----|------------|----------|---------|----------|
| **A1** | Categories empty/wrong at sync | `catalog_sync.py` L151 reads only `metadata.categories`; top-level API `categories` ignored | Metadata path + `list_document_ids_by_categories` empty | **T1:** Merge top-level + metadata categories in `catalog_sync` |
| **A2** | Empty categories on manual ingest | Before P26, `categories: []` persisted | Category filter misses playbook | **Done:** `resolve_ingest_categories` in `ingest.py` L49 — verify sync uses ingest path |
| **A3** | `applies_to_contract_types` wrong | `pgvector_store.py` L545-546 filters by contract type | Valid policy excluded in discovery/retrieval | **T2:** Validate + warn at ingest; document Java/catalog contract |
| **A4** | Policy not indexed / stale | `sync_policy_from_catalog` skips reindex when `index_status=indexed` L88-101 | Old playbook text in vector store | **T3:** `force_reindex` on catalog version change; optional `POLICY_STALE_DAYS` |
| **A5** | Registry ↔ index mismatch | Discovery scope uses `list_policy_registry` | Scope IDs not found | **T4:** Preflight/registry check: scoped IDs exist and `index_status=indexed` |

### Layer B — Config (semantic search off)

| ID | Root cause | Evidence | Symptom | Solution |
|----|------------|----------|---------|----------|
| **B1** | `search_backend=lexical` default | `document_core/config.py` L24 | Dense path not fully semantic | **T5:** Production env `SEARCH_BACKEND=hybrid` |
| **B2** | CI/tests use lexical only | `review-ci.yml`, `conftest.py` | Prod misconfigured by copy-paste | **T5:** Document prod profile; keep CI lexical |
| **B3** | `review_agent/.env.production.example` missing hybrid | File has no `SEARCH_BACKEND` | document-mcp deployed lexical | **T5:** Extend examples |

### Layer C — Discovery caps & routing (auto mode only)

| ID | Root cause | Evidence | Symptom | Solution |
|----|------------|----------|---------|----------|
| **C1** | Group cap drops playbooks | `discovery_max_policy_groups=6` default `config.py` L54 | Whole category group missing | **T6:** Raise caps for “wide discovery” profile OR use explicit scope |
| **C2** | Topic cap | `discovery_max_topics=8` L58 | Topics never searched | **T6:** Raise `DISCOVERY_MAX_TOPICS` / ceiling |
| **C3** | `discovery_min_score` | L61 `0.08` | Low-score policies dropped | Lower to `0.05` for recall mode |
| **C4** | Weak routing topics | `contract_routing` lexical/LLM | Discovery searches wrong queries | `CONTRACT_ROUTING_MODE=llm` in prod |
| **C5** | No topics + no sections | `discover_policies_from_topics` L534-536 | Discovery skipped entirely | Ensure contract parses to sections |

### Layer D — Section retrieval (policy in scope, chunk missed)

| ID | Root cause | Evidence | Symptom | Solution |
|----|------------|----------|---------|----------|
| **D1** | Hard category filter, no matches | `multi_retrieval.py` L148-154 | Empty filter_doc_ids | **Done:** `retrieval_category_filter_fallback=true` default |
| **D2** | Classifier → `general` only | `section_classifier` | Metadata path weak | Lexical-first default; monitor warnings |
| **D3** | Recall too narrow | `retrieval_recall_top_k=20` | Right chunk not in union | Raise to 25-30 in prod profile |
| **D4** | One doc dominates | Per-doc cap | Missing alternate policy | `retrieval_max_hits_per_document=2-3` (already default 3) |
| **D5** | Retries exhausted | 3 attempts | `retrieval_zero_hit_sections > 0` | **T7:** Alert on artifact field (ops); optional bump `retrieval_max_attempts` |

---

## 4. Task map (minimal implementation)

| # | Task | Files | LOC | Mode |
|---|------|-------|-----|------|
| **T1** | ~~Catalog category merge~~ | ~~`catalog_sync.py`~~ | — | **Superseded by P36** — Java sends `categories` on ingest |
| **T2** | Ingest warning for empty categories post-resolve | `ingest.py` | ~8 | All |
| **T3** | ~~Reindex on catalog hash change~~ | ~~`catalog_sync.py`~~ | — | **Superseded by P36** — Java re-ingest on content change |
| **T4** | Preflight: scoped/indexed policy check | `review_preflight.py` | ~30 | Explicit scope |
| **T5** | Production env profile (hybrid + caps) | `.env.production.example`, `document_core/.env.example` | ~25 | Config |
| **T6** | “Wide discovery” settings doc + optional preset | plan + env comments only | 0 | Auto discovery |
| **T7** | Integration test: N scoped policies → N discovered | `test_policy_discovery.py` or `test_review_e2e.py` | ~40 | Test |
| **T8** | Zero-hit regression test (seeded policy → hits) | `test_review_e2e.py` | ~10 | **Done** (P32) |

**Ship order:** T1 → T5 → T2 → T7 → T4 → T3 (T3 optional if catalog has version field).

---

## 5. T1 — Catalog category merge (`catalog_sync.py`)

### Problem

```python
# L151 today — misses top-level categories from Java API
categories = normalize_categories(list(payload.metadata.get("categories") or []))
```

### Change

```python
def _resolve_catalog_categories(data: dict) -> list[str]:
    top = data.get("categories") if isinstance(data.get("categories"), list) else []
    meta = (data.get("metadata") or {}).get("categories")
    meta_list = meta if isinstance(meta, list) else []
    return normalize_categories([*top, *meta_list])

# fetch_policy_from_catalog: pass through
# sync: categories = _resolve_catalog_categories(data)  # from raw JSON before payload
```

Ingest still runs `resolve_ingest_categories` — explicit non-empty categories win.

### Test

`document_core/tests/test_catalog_sync_categories.py` — top-level only, metadata only, both merged.

---

## 6. T2 — Warn on weak policy metadata (`ingest.py`)

After `resolve_ingest_categories` for POLICY:

```python
if categories == ["general"] and request.kind == DocumentKind.POLICY:
    warnings.append("policy_categories_general_only: metadata path may miss this playbook")
```

Surfaces in ingest warnings → index logs.

---

## 7. T4 — Preflight scoped policy check (`review_preflight.py`)

When `policy_document_ids` provided:

```python
registry = await client.list_policy_registry(tenant_id, kind="policy")
indexed = {str(r.document_id) for r in registry.policies if r.index_status == "indexed"}
missing = [pid for pid in policy_document_ids if pid not in indexed]
if missing:
    raise FatalPipelineError(f"scoped policies not indexed: {missing[:5]}")
```

Fast fail before 20 sections × retrieval timeouts.

---

## 8. T5 — Production config profile (no code)

### document-mcp / `document_core` (`.env` on server)

```env
DOCUMENT_STORE_BACKEND=pgvector
DATABASE_URL=postgresql://...

# Layer B — turn on semantic search
SEARCH_BACKEND=hybrid
SEARCH_HYBRID_ALPHA=0.55
EMBEDDING_ENABLED=true
EMBEDDING_MODEL=nomic-ai/modernbert-embed-base

RERANKER_ENABLED=true
RERANKER_BACKEND=cross_encoder
RETRIEVAL_RECALL_TOP_K=25
RETRIEVAL_FINAL_TOP_K=10

# Optional staleness (Phase 28)
POLICY_STALE_DAYS=90
```

### review_agent (`.env`)

```env
REVIEW_POLICY_SCOPE=discovered
CONTRACT_ROUTING_MODE=llm
REVIEW_PREFLIGHT_ENABLED=true

# Layer C — wide discovery (auto mode)
DISCOVERY_MAX_POLICY_GROUPS=20
DISCOVERY_MAX_POLICY_GROUPS_CEILING=30
DISCOVERY_MAX_TOPICS=15
DISCOVERY_MIN_SCORE=0.05
DISCOVERY_SECTION_CATEGORY_SWEEP=true
DISCOVERY_CATEGORY_RESERVE_SLOTS=true
DISCOVERY_MAX_POLICIES=0

# Layer D — section retrieval
RETRIEVAL_RECALL_TOP_K=25
RETRIEVAL_FINAL_TOP_K=10
RETRIEVAL_MAX_ATTEMPTS=3
RETRIEVAL_CATEGORY_HARD_FILTER=true
RETRIEVAL_CATEGORY_FILTER_FALLBACK=true
```

### Guaranteed all policies in a known set

```env
REVIEW_POLICY_SCOPE=request
```

API/request must pass **complete** `policy_document_ids: [uuid, ...]`. Discovery uses `seed_discovered_from_scope` — **no group cap** (`policy_discovery.py` L581-592).

---

## 9. Operational verification (no new code)

After each review, check artifact / report:

| Field | Healthy | Action if bad |
|-------|---------|---------------|
| `discovery.document_ids` / `discovered_policy_document_ids` | Contains expected playbooks | Fix scope, caps, routing, or sync |
| `retrieval_zero_hit_sections` | `0` | Fix categories, hybrid, classifier |
| `degraded_sections` | Empty or explained | MCP/LLM outages |
| `compliance_stats.node_timings_ms` | No single node dominates | Perf (P34) |
| Warnings | No `policy_categories_general_only` | Re-tag policies |

---

## 10. Definition of done

- [ ] T1: Catalog sync merges top-level + metadata categories; test passes
- [ ] T5: Production example env files document hybrid + wide discovery profile
- [ ] Explicit scope: all `policy_document_ids` appear in discovery output (T7 test)
- [ ] Integration: `policy_hits >= 1` for seeded liability policy (P32 test)
- [ ] `retrieval_zero_hit_sections` documented in ops runbook
- [ ] (Optional T3) Catalog content change triggers reindex

---

## 11. What NOT to do (yet)

| Idea | Why defer |
|------|-----------|
| Fourth search API | 3-path + rerank is sufficient after Layer A+B |
| RRF / MMR fusion | Phase 34+ enhancement |
| Discovery uses full `multi_retrieval` | Large refactor; category sweep partially covers |
| Load all tenant policies always | Wrong product model; use explicit scope |
| Java changes | T1 handles top-level categories in Python if API sends them |

---

## 12. Priority summary (for you)

```
Must do (data):
  1. Every policy indexed with correct categories + contract types
  2. T1 catalog category merge
  3. Catalog sync / reindex on update

Must do (config):
  4. SEARCH_BACKEND=hybrid + embeddings on
  5. Cross-encoder rerank on

Choose discovery mode:
  6a. Guaranteed set → policy_document_ids + REVIEW_POLICY_SCOPE=request
  6b. Auto best-effort → wide discovery caps + LLM routing

Monitor:
  7. retrieval_zero_hit_sections, degraded_sections, discovery warnings
```

---

## 13. Files reference

| Area | Key files |
|------|-----------|
| Discovery | `review_agent/services/policy_discovery.py`, `graph/discovery_nodes.py` |
| Section retrieval | `review_agent/services/multi_retrieval.py` |
| Category SQL | `document_core/store/pgvector_store.py` `list_document_ids_by_categories` |
| Ingest tags | `document_core/services/metadata_at_ingest.py`, `ingest.py` |
| Catalog sync | `document_core/services/catalog_sync.py` |
| Config | `document_core/config.py`, `review_agent/config.py` |
| Artifact metrics | `review_agent/services/review_artifact.py`, `graph/section_retrieval_nodes.py` |
