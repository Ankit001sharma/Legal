# Phase 10 — Unified Production Implementation Plan (10A + 10B)

**Plan ID:** `DR-PHASE-10-PROD`  
**Replaces:** dual-mode v1 (`legacy` + `section_first` flag)  
**Goal:** **One pipeline only** — section-first retrieval + section-first LLM compare. Remove legacy policy-first code. No fallbacks. Production defaults.  
**Status:** Implemented — single section-first pipeline; legacy code removed  
**Parent docs:** [PHASE10_SECTION_FIRST_RETRIEVAL_PLAN.md](./PHASE10_SECTION_FIRST_RETRIEVAL_PLAN.md), [PHASE10B_SECTION_FIRST_LLM_REVIEW_IMPL_PLAN.md](./PHASE10B_SECTION_FIRST_LLM_REVIEW_IMPL_PLAN.md)

---

## 0. Executive decision

| Before (v1) | After (production) |
|-------------|-------------------|
| `REVIEW_PIPELINE_MODE=legacy` (default) | **Remove flag** — only section-first graph |
| `compliance_mode=lexical\|llm\|hybrid` | **Remove** — only `section_compare_llm` |
| `section_classify_mode=lexical\|llm` | **Remove** — only LLM classifier (batched) |
| `policy_plan` → `policy_retrieval` → category compare | **Delete** — caused RC-1 |
| `final_gap_verify` stub | **Implement** — required, not optional |
| Reranker no-op | **Ship** cross-encoder or API reranker (config on) |
| Dual test suites | **Migrate** all E2E to section-first; delete legacy tests |

**Principle:** One code path, one default, one mental model. Quote verification stays deterministic; judgment stays LLM.

---

## 1. Root causes → single-pipeline fix map

| ID | Problem | Legacy behavior (remove) | Production fix (keep/build) |
|----|---------|--------------------------|----------------------------|
| RC-1 | Policy-first compare | `policy_plan` → search contract per policy category | Contract section anchor → retrieve policies per section |
| RC-2 | `top_k=5` | `policy_search_top_k=5` in `resolve_policy_hits` | `retrieval_recall_top_k=20`, `retrieval_final_top_k=10` |
| RC-3 | Discovery cap 8 | `discovery_max_policies=8` | Raise to `discovery_max_policies=50` OR remove cap when `tenant_auto` |
| RC-4 | Plan cap 30 | `review_max_categories=30` | **N/A** — no plan node |
| RC-5 | No `categories[]` | metadata filter useless | Ingest + Java sync + `catalog_sync` pass categories |
| RC-6 | No reranker | hybrid score only | `rerank_hits()` with cross-encoder default **on** |
| RC-7 | Single search path | one `search_policy` call | dense + FTS + metadata union |
| RC-8 | Heuristic parser | bad inline sections | Warn + require indexed policies in prod; Phase 8 parser later |
| RC-9 | 12k truncation | `compliance_max_section_chars` in compare | `section_compare_max_section_chars=32000`; batch budget 48k |
| RC-10 | Category batching | `compliance_batch_llm` | Section batch size 2 |
| RC-11 | No `section_id` on findings | dimension-only | `SectionCompareItem` → `ComplianceFinding` |
| RC-12 | Silent skip no-policy | sections dropped in compare node | merge gap + `final_verify_llm` re-retrieve |
| RC-13 | No dedupe | duplicate findings | merge key `(section_id, policy_document_id, dimension_label)` |
| RC-14 | No final pass | stub node | `final_verify_llm.py` full implementation |
| RC-15 | Hallucinated quotes | ungrounded report | `_validate_and_normalize_quotes` + `grounding_node` |

---

## 2. Target production flow (only flow)

```text
START
  → load_memory
  → contract_parser          # ingest_document → pgvector
  → clause_detection         # list_sections → contract_sections[]
  → [tenant_auto only]
      contract_routing → policy_discovery
  → index_policies           # refs, discovered, inline (discouraged in prod)
  → section_policy_retrieval # 10A — classify (LLM) + multi_retrieve + rerank
  → section_compare_llm      # 10B — batch 2, full section text
  → merge_section_findings   # dedupe + gaps + unclear + conflicts
  → final_gap_verify         # re-retrieve + LLM on gaps only
  → grounding                # verify_quote (unchanged)
  → report
  → save_memory
END
```

**No branches.** No `if compliance_mode`. No `if review_pipeline_mode`.

---

## 3. Plan 1 (10A) — implementation status & remaining work

### 3.1 Already implemented ✅

| Component | File(s) | Notes |
|-----------|---------|-------|
| Taxonomy | `document_core/schemas/taxonomy.py` | `STANDARD_POLICY_CATEGORIES`, `normalize_categories` |
| Categories on ingest | `chunk.py`, `ingest.py` | `IngestRequest.categories` |
| GIN migration | `migrations/004_policy_categories.sql` | metadata index |
| FTS path | `pgvector_store.search_children_fts`, `search_policy_fts` | |
| Recall path | `search_policy_recall` | top_k=20 |
| Metadata path | `search_policy_by_categories`, `list_document_ids_by_categories` | |
| Union + rerank interface | `multi_retrieval.py`, `search/reranker.py` | reranker = no-op today |
| Section classifier | `section_classifier.py` | **has lexical fallback — remove** |
| Retrieval node | `section_retrieval_nodes.py` | |
| MCP endpoints | `document_server/main.py` | 3 search tools |
| Client | `document_client.py` | |
| Test | `document_core/tests/test_taxonomy.py` | |

### 3.2 10A — remaining tasks (production)

#### 10A-P1 — Categories end-to-end (RC-5)

| Subtask | Action | File | Est. |
|---------|--------|------|------|
| 10A-P1.1 | Pass `categories` from catalog document into `IngestRequest` | `catalog_sync.py` | 25 |
| 10A-P1.2 | `register_policy` MCP accepts `metadata.categories` | `document_server/main.py` | 20 |
| 10A-P1.3 | Auto-infer categories on ingest when missing (LLM once per policy doc, not per section) | `ingest.py` or new `policy_category_tagger.py` | 80 |
| 10A-P1.4 | Integration test: index with categories → metadata path returns doc | `tests/test_multi_retrieval.py` | 100 |

**Acceptance:** Policy without manual categories still gets tags at index time; metadata path returns hits.

---

#### 10A-P2 — Classifier: LLM only, batched (no lexical fallback)

| Subtask | Action | File | Est. |
|---------|--------|------|------|
| 10A-P2.1 | **Delete** `classify_section_lexical`, `policy_category_hints.yaml` usage | `section_classifier.py` | 30 |
| 10A-P2.2 | **Delete** `section_classify_mode` from config | `config.py` | 5 |
| 10A-P2.3 | Batch classify 2 sections per LLM call (mirror compare batching) | `section_classifier.py` | 60 |
| 10A-P2.4 | On LLM failure: **fail section** with warning (no keyword fallback) | same | 20 |
| 10A-P2.5 | Default: `section_classify_batch_size=2` | `config.py` | 5 |
| 10A-P2.6 | Update tests — mock LLM only | `test_section_classifier.py` | 40 |

**Acceptance:** No code path reads YAML hints; classifier always uses `section_policy_classify.md`.

---

#### 10A-P3 — Reranker production (RC-6)

| Subtask | Action | File | Est. |
|---------|--------|------|------|
| 10A-P3.1 | Implement `CrossEncoderReranker` or HTTP rerank API wrapper | `document_core/search/reranker_service.py` | 120 |
| 10A-P3.2 | Default `RERANKER_ENABLED=true` in `.env.example` | `document_core/.env.example` | 5 |
| 10A-P3.3 | Wire in `multi_retrieval.py` (already calls `rerank_hits`) | verify | 10 |
| 10A-P3.4 | Test: order changes with mock scores | `tests/test_reranker.py` | 60 |

**Acceptance:** Union 40 hits → rerank → exactly 10 parents to compare LLM.

---

#### 10A-P4 — Retrieval hardening

| Subtask | Action | File | Est. |
|---------|--------|------|------|
| 10A-P4.1 | Per-node retrieval cache `(tenant_id, document_id)` for parent chunks | `section_retrieval_nodes.py` | 40 |
| 10A-P4.2 | Raise `discovery_max_policies` default to 50 | `config.py` | 5 |
| 10A-P4.3 | `test_multi_retrieval.py`: FTS finds keyword-only policy | tests | 120 |
| 10A-P4.4 | Report `retrieval_meta` per section in compliance_stats | `section_retrieval_nodes.py` | 20 |

---

### 3.3 10A checklist

```
[x] Taxonomy + ingest categories
[x] FTS + recall + metadata search
[x] multi_retrieval union
[x] section_policy_retrieval_node
[ ] catalog_sync categories
[ ] LLM-only classifier (remove lexical)
[ ] Production reranker
[ ] test_multi_retrieval.py
[ ] Retrieval cache
```

---

## 4. Plan 2 (10B) — implementation status & remaining work

### 4.1 Already implemented ✅

| Component | File(s) | Notes |
|-----------|---------|-------|
| Graph section-first branch | `review_graph.py` | **still dual-mode — simplify to only branch** |
| Section compare LLM | `section_compare_llm.py` | uses 12k truncate — fix |
| Token budget | `token_budget.py` | batch split works |
| Merge + dedupe + NO_POLICY gap | `section_merge.py` | fixed double-gap |
| Compare node | `section_compare_nodes.py` | |
| Prompts | `section_compare.md`, `final_gap_verify.md` | gap prompt unused |
| Schemas | `section_compare.py`, `section_retrieval.py`, `section_classify.py` | |
| E2E mock | `test_review_e2e_section_first.py` | |
| Unit tests | `test_section_merge.py`, `test_section_classifier.py` | |

### 4.2 10B — remaining tasks (production)

#### 10B-P1 — Compare LLM hardening (RC-9, RC-10, RC-15)

| Subtask | Action | File | Est. |
|---------|--------|------|------|
| 10B-P1.1 | Add `section_compare_max_section_chars=32000` — stop using `compliance_max_section_chars` | `config.py`, `section_compare_llm.py` | 25 |
| 10B-P1.2 | Wire `gather_limited` for batches (`section_compare_concurrency=3`) | `section_compare_llm.py` | 30 |
| 10B-P1.3 | Pass `memory_context` into compare user prompt | `section_compare_llm.py`, node | 25 |
| 10B-P1.4 | Backfill `policy_document_id` from hit map when LLM omits | `section_compare_llm.py` | 25 |
| 10B-P1.5 | LLM failure → `INSUFFICIENT_POLICY_CONTEXT` per section (not `[]`) | same | 30 |
| 10B-P1.6 | Extract quote helpers from `compliance_llm.py` → `services/quote_validate.py` | new file | 40 |
| 10B-P1.7 | `test_section_compare.py` | tests | 120 |

---

#### 10B-P2 — Merge quality (RC-12, RC-13)

| Subtask | Action | File | Est. |
|---------|--------|------|------|
| 10B-P2.1 | UNCLEAR bucket: `INCONCLUSIVE` or `confidence < 0.5` | `section_merge.py` | 40 |
| 10B-P2.2 | Conflict detection: same `dimension_label`, different `status` | same | 50 |
| 10B-P2.3 | State: `gap_section_ids`, `unclear_finding_ids`, `conflict_pairs` | `review_state.py` | 20 |
| 10B-P2.4 | Extend merge tests | `test_section_merge.py` | 40 |

---

#### 10B-P3 — Final gap verify (RC-12, RC-14) — **critical**

| Subtask | Action | File | Est. |
|---------|--------|------|------|
| 10B-P3.1 | **Create** `services/final_verify_llm.py` | new | 150 |
| 10B-P3.2 | Input: gap sections + unclear + conflicts from merge | same | |
| 10B-P3.3 | Re-retrieve gaps with `recall_top_k=30` via `multi_retrieve_for_section` | same | 40 |
| 10B-P3.4 | If new hits → `compare_section_batch([section])` | same | 30 |
| 10B-P3.5 | If still empty → LLM `final_gap_verify.md` → confirm INSUFFICIENT | same | 40 |
| 10B-P3.6 | Replace stub in `final_gap_verify_node` | `section_compare_nodes.py` | 30 |
| 10B-P3.7 | `test_final_gap_verify.py` | tests | 90 |

**Acceptance:** Zero gap sections without explicit final status in report.

---

#### 10B-P4 — Report & observability

| Subtask | Action | File | Est. |
|---------|--------|------|------|
| 10B-P4.1 | Aggregate stats: `sections_reviewed`, `sections_no_policy`, `llm_batches`, path counts | `nodes.report_node` | 40 |
| 10B-P4.2 | Remove `review_pipeline_mode` from metadata (always section-first) | same | 5 |
| 10B-P4.3 | Generator: section-first summary block | `reports/generator.py` | 30 |
| 10B-P4.4 | Warnings: truncation, parser confidence, gap count | merge + compare nodes | 20 |

---

### 4.3 10B checklist

```
[x] section_compare_llm + token budget
[x] merge_section_findings (basic)
[x] graph section-first branch (dual-mode)
[ ] final_verify_llm.py
[ ] compare concurrency + 32k cap
[ ] memory_context in compare
[ ] UNCLEAR + conflicts
[ ] test_section_compare.py
[ ] test_final_gap_verify.py
[ ] full report stats
```

---

## 5. Legacy removal plan (single pipeline)

### 5.1 Graph simplification

**File:** `review_agent/graph/review_graph.py`

| Remove | Keep |
|--------|------|
| `policy_plan_node`, `policy_retrieval_node` registration | `contract_parser`, `clause_detection`, `index_policies` |
| `compliance_review_node` | `section_policy_retrieval`, `section_compare_llm`, `merge_section_findings`, `final_gap_verify` |
| Entire `hybrid_nodes` import + edges | `grounding`, `report`, `save_memory`, memory nodes |
| `if section_first` / `elif tenant_auto` / legacy branches | Single edge list |
| `review_pipeline_mode` check | `tenant_auto` → routing → discovery → index only |

**Target `build_review_graph` (~60 lines):**

```python
# Pseudocode — one path only
graph.add_edge("clause_detection", ...)
if tenant_auto:
    graph.add_edge(..., "contract_routing")
    graph.add_edge("contract_routing", "policy_discovery")
    graph.add_edge("policy_discovery", "index_policies")
else:
    graph.add_edge("clause_detection", "index_policies")
graph.add_edge("index_policies", "section_policy_retrieval")
graph.add_edge("section_policy_retrieval", "section_compare_llm")
graph.add_edge("section_compare_llm", "merge_section_findings")
graph.add_edge("merge_section_findings", "final_gap_verify")
graph.add_edge("final_gap_verify", "grounding")
graph.add_edge("grounding", "report")
graph.add_edge("report", "save_memory")
```

---

### 5.2 Files to DELETE (legacy policy-first pipeline)

| File | Reason |
|------|--------|
| `graph/hybrid_nodes.py` | Hybrid compliance — replaced by section compare |
| `services/policy_plan.py` | Dynamic/static plan — RC-4 |
| `services/policy_plan_llm.py` | Plan LLM filter |
| `services/policy_retrieval.py` | Category retrieval — RC-1, RC-2 |
| `services/compliance.py` | Lexical compare |
| `services/compliance_batch.py` | Hybrid batching |
| `services/compliance_batch_llm.py` | Hybrid LLM |
| `services/compliance_prescreen.py` | Hybrid prescreen |
| `services/compliance_merge.py` | Hybrid merge |
| `services/gap_retrieval.py` | Hybrid gap pass |
| `services/alignment.py` | Hybrid alignment scores |
| `schemas/policy_plan_llm.py` | Plan schema |
| `schemas/gap_request.py` | Hybrid gap |
| `schemas/alignment.py` | Hybrid alignment |
| `prompts/policy_plan.md` | Unused |
| `prompts/compliance_review.md` | Replaced by `section_compare.md` |
| `prompts/compliance_review_batch.md` | Hybrid |
| `dimensions/review_dimensions.yaml` | Static 5-dimension plan |
| `data/policy_category_hints.yaml` | Lexical classifier fallback |

---

### 5.3 Files to TRIM (keep partial)

| File | Keep | Remove |
|------|------|--------|
| `graph/nodes.py` | `contract_parser`, `clause_detection`, `index_policies`, `grounding`, `report` | `policy_plan_node`, `policy_retrieval_node`, `compliance_review_node` |
| `services/compliance_llm.py` | Move quote helpers to `quote_validate.py` | `compare_sections_llm` and category compare |
| `state/review_state.py` | section-first fields + core fields | `review_categories`, `policy_hits_by_category`, hybrid fields |
| `config.py` | section-first + LLM + discovery settings | `compliance_mode`, `review_pipeline_mode`, `review_plan_mode`, hybrid knobs, `policy_search_top_k` |

---

### 5.4 Tests to DELETE or REWRITE

| Delete | Rewrite as section-first |
|--------|--------------------------|
| `test_policy_plan.py` | — |
| `test_policy_plan_llm.py` | — |
| `test_policy_retrieval.py` | `test_section_retrieval.py` (new) |
| `test_compliance_lexical.py` | — |
| `test_compliance_batch.py` | — |
| `test_compliance_prescreen.py` | — |
| `test_compliance_merge.py` | — |
| `test_compliance_llm.py` | `test_quote_validate.py` (helpers only) |
| `test_review_e2e.py` | merge into `test_review_e2e.py` (one E2E) |

**`conftest.py`:** Remove `COMPLIANCE_MODE=lexical` and `REVIEW_PIPELINE_MODE=legacy` autouse overrides.

---

### 5.5 Config defaults (production — single path)

**Remove env vars:**

```text
REVIEW_PIPELINE_MODE
COMPLIANCE_MODE
REVIEW_PLAN_MODE
REVIEW_MAX_CATEGORIES
REVIEW_PLAN_LLM_FILTER*
POLICY_SEARCH_TOP_K
SECTION_CLASSIFY_MODE
COMPLIANCE_BATCH_SIZE
COMPLIANCE_PRESCREEN_*
COMPLIANCE_GAP_PASS_ENABLED
```

**New / keep defaults:**

```env
# review_agent/.env — production defaults
SECTION_CLASSIFY_BATCH_SIZE=2
SECTION_COMPARE_BATCH_SIZE=2
SECTION_COMPARE_MAX_TOKENS=48000
SECTION_COMPARE_MAX_SECTION_CHARS=32000
SECTION_RETRIEVAL_CONCURRENCY=8
SECTION_COMPARE_CONCURRENCY=3
RETRIEVAL_RECALL_TOP_K=20
RETRIEVAL_FINAL_TOP_K=10
FINAL_GAP_VERIFY_ENABLED=true
FINAL_GAP_RECALL_TOP_K=30
REVIEW_MIN_SECTION_CHARS=40
DISCOVERY_MAX_POLICIES=50

COMPLIANCE_LLM_TEMPERATURE=0
COMPLIANCE_LLM_MAX_TOKENS=2048
```

**document_core/.env:**

```env
RERANKER_ENABLED=true
RETRIEVAL_RECALL_TOP_K=20
RETRIEVAL_FINAL_TOP_K=10
```

---

## 6. State model (simplified)

### Remove from `ReviewState`

```text
review_categories
policy_hits_by_category
contract_hits_by_category
retrieval_meta_by_category
alignment_by_category
prescreen_findings
deferred_category_ids
pass1_findings
pass2_findings
gap_requests
gap_hits_by_request
```

### Keep / add

```text
# Core
tenant_id, contract_text, ingest_result, contract_sections
indexed_policies, findings, grounded_findings, warnings, report

# Discovery (tenant_auto only)
contract_routing, discovered_policies, discovered_policy_document_ids

# Section-first (always)
section_retrieval_by_id
section_review_sections
section_compare_items
gap_section_ids
unclear_finding_ids
conflict_pairs
compliance_stats
final_verify_stats

# Memory (Phase 9)
memory_context, memory_hits, thread_id
```

---

## 7. Implementation sprints (ordered)

### Sprint 1 — Complete 10B critical path (1 week)

1. `final_verify_llm.py` + wire node  
2. Compare hardening (32k cap, concurrency, memory, failure handling)  
3. `test_section_compare.py`, `test_final_gap_verify.py`  

**Gate:** E2E with mock LLM passes; gap sections always have final status.

---

### Sprint 2 — Complete 10A production (1 week)

1. Remove lexical classifier; LLM batch classify  
2. `catalog_sync` categories + policy auto-tag on ingest  
3. `test_multi_retrieval.py`  
4. Reranker service + enable by default  

**Gate:** Metadata path proven in test; reranker reduces top_k to 10.

---

### Sprint 3 — Legacy deletion (3–4 days)

1. Simplify `review_graph.py` to single path  
2. Delete files in §5.2  
3. Trim `nodes.py`, `config.py`, `review_state.py`  
4. Extract `quote_validate.py`; delete `compliance_llm.py` body  
5. Rewrite `conftest.py` + migrate E2E tests  
6. Update `review/README.md`, `.env.example`, `.env.production.example`  

**Gate:** No imports of deleted modules; `pytest tests/ -q` green with Postgres.

---

### Sprint 4 — QA & rollout (3 days)

1. Golden fixture: NDA + 2 policies → ≥1 NON_COMPLIANT  
2. Streamlit / platform smoke test  
3. Staging deploy with new defaults  
4. Java: `metadata.categories[]` on all policy syncs  

---

## 8. What we keep unchanged (explicit)

| Component | Why |
|-----------|-----|
| `ingest_document` / `list_sections` | Storage contract |
| `index_policies_node` | Policy loading |
| `policy_discovery` + `contract_routing` | `tenant_auto` without plan node |
| `grounding_node` | Quote verification |
| `memory_nodes` | Phase 9 session |
| `finding_enrich.py` | Policy titles in report |
| `document_core` pgvector hybrid | Extended, not replaced |
| Java catalog client | Policy fetch |

---

## 9. Risks of single-pipeline cutover

| Risk | Mitigation |
|------|------------|
| Breaking existing deployments on `legacy` | Sprint 3 in feature branch; tag release `v2-section-first` |
| LLM cost (classify + compare per section) | Batch 2 for both; skip short sections |
| Classifier LLM-only fails without API key | Fail fast at startup if `LLM_API_KEY` missing |
| Test suite churn | Rewrite E2E before deleting legacy tests |
| Inline pasted policies still weak | Prod warning + docs: use catalog index |

---

## 10. Acceptance criteria (Phase 10 production DONE)

### Functional

- [ ] One graph path — no `REVIEW_PIPELINE_MODE` in codebase  
- [ ] Every contract section ≥ `review_min_section_chars` reviewed or explicitly gap-reported  
- [ ] Findings have `contract_section_id`, `policy_document_id`, grounded quotes  
- [ ] Final gap pass runs on all NO_POLICY + UNCLEAR sections  
- [ ] Union retrieval + reranker → ≤10 policy parents per section  

### Code quality

- [ ] No dead imports from deleted legacy modules  
- [ ] No lexical/hybrid fallback code paths  
- [ ] `pytest tests/ -q` passes with Postgres  
- [ ] `.env.example` reflects single-pipeline defaults only  

### Ops

- [ ] Java sends `metadata.categories[]`  
- [ ] `RERANKER_ENABLED=true` in prod document_core env  
- [ ] Report includes section-first ops stats  

---

## 11. File map summary

### New (production completion)

| Path |
|------|
| `review_agent/services/final_verify_llm.py` |
| `review_agent/services/quote_validate.py` |
| `review_agent/tests/test_section_compare.py` |
| `review_agent/tests/test_final_gap_verify.py` |
| `review_agent/tests/test_multi_retrieval.py` |
| `review_agent/tests/test_section_retrieval.py` |
| `document_core/search/reranker_service.py` |
| `document_core/tests/test_reranker.py` |

### Delete (~15 files, ~4k lines removed)

See §5.2.

### Modify

| Path | Change |
|------|--------|
| `review_graph.py` | Single path ~60 lines |
| `config.py` | Remove legacy settings; production defaults |
| `review_state.py` | Remove hybrid fields |
| `nodes.py` | Remove 3 legacy nodes |
| `section_classifier.py` | LLM-only batched |
| `section_compare_llm.py` | Production hardening |
| `section_merge.py` | UNCLEAR + conflicts |
| `catalog_sync.py` | categories |
| `conftest.py` | No legacy overrides |

---

**Total estimate:** ~2,200 lines new/modify + ~4,000 lines deleted = **net simpler codebase**.  
**Critical path:** 10B-P3 (final verify) → Sprint 3 (legacy delete) → QA.

*One pipeline. Section-first anchor. Multi-path recall. LLM judgment. Deterministic quotes only.*
