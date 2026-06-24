# Sprint 3 — Retrieval “Never Miss” & Ops Visibility (P3.1–P3.4)

**Plan ID:** `DR-PHASE-12-P3`  
**Scope:** Review agent retrieval layer only (`multi_retrieval`, `section_retrieval_nodes`, `policy_discovery`, config)  
**Goal:** Policies that exist in the tenant index are found per section; ops can see *why* retrieval succeeded/failed per section.  
**Depends on:** Phase 10A multi-path retrieval, Phase 10 section classifier, tenant_auto discovery  
**Estimate:** ~220 lines prod code, ~180 lines tests, **2–3 days**  
**Out of scope:** New reranker model, tombstone (P2.3), catalog fetch ladder (Phase 2), graph node changes

---

## 0. Problem statement (verified in code)

| ID | Requirement | Current state | Gap |
|----|-------------|---------------|-----|
| P3.1 | Retry loop: attempt 1 → broaden query (2 retries) → log steps | **Single shot** in `multi_retrieval.py` L55–114: classify → 3 paths → union → rerank; **no retry** when `final_count=0` | Policy in DB but query/classifier mismatch → section gets `policy_hits=[]` → gap LLM only |
| P3.2 | Typed clause map: classifier categories on bundle; **hard filter** before vector search | Categories stored on `SectionRetrievalBundle.categories` + `retrieval_meta["categories"]` only; **dense + FTS search entire tenant** (`SearchRequest` without `document_ids`) | Wrong-family policies pollute union; category path is soft (parallel), not a gate |
| P3.3 | Raise / configurable `discovery_max_policies`; warn when capped | Default **50** in `config.py` L36; cap in `policy_discovery.py` L77; **no warning** when `len(ranked) > cap` | Large tenants silently drop policies from discovery scope |
| P3.4 | Per-section retry stats in `retrieval_meta` | `retrieval_meta` has path counts only (`dense_count`, `fts_count`, …); **no attempt ladder** | Ops cannot debug “why no policy for §12.2” |

**Related inefficiency (fix in P3.2 pass):** `multi_retrieve_for_section` calls `classify_section_policies` **per section** (N LLM calls). Batch classify once in `section_retrieval_nodes` and pass result in.

---

## 1. Design principles

1. **Retry only when empty** — stop early when `final_count > 0` (save latency + MCP calls).
2. **Never miss > precision on retries** — attempt 0 uses category hard filter; later attempts broaden query then drop filter (documented in meta).
3. **Scope to discovered policies** — intersect category filter with `state.policy_document_ids` (discovery output) so search stays within the review’s policy universe.
4. **No new graph nodes** — all logic inside `multi_retrieval.py` + thin wiring in `section_retrieval_nodes.py`.
5. **Structured meta** — stable JSON keys for report + `compliance_stats` aggregation (Java/log pipelines can parse).

---

## 2. Environment / config

| Variable | Default | Purpose |
|----------|---------|---------|
| `RETRIEVAL_MAX_ATTEMPTS` | `3` | P3.1 — initial + 2 retries |
| `RETRIEVAL_BROADEN_ON_RETRY` | `true` | Enable query broadening ladder |
| `RETRIEVAL_CATEGORY_HARD_FILTER` | `true` | P3.2 — restrict dense/FTS to category doc IDs on attempt 0 |
| `RETRIEVAL_CATEGORY_FILTER_FALLBACK` | `true` | If category filter → 0 doc IDs, retry without filter |
| `DISCOVERY_MAX_POLICIES` | `50` | P3.3 — **`0` = unlimited** |
| `DISCOVERY_WARN_ON_CAP` | `true` | Emit warning when results truncated |

Add to `review_agent/.env.example`. Existing `RETRIEVAL_RECALL_TOP_K` / `RETRIEVAL_FINAL_TOP_K` unchanged.

---

## 3. Task breakdown

### P3.1 — Structured retry loop in `multi_retrieval.py`

#### 3.1.1 Retry ladder (deterministic, no extra LLM)

```text
Attempt 0 (primary)
  query     = classification.query_terms[0] or section.title
  filter    = category doc IDs ∩ scope doc IDs (P3.2)
  paths     = dense + FTS + metadata (parallel)

Attempt 1 (broaden) — only if final_count == 0
  query     = query_terms[1] if len > 1
              else section.title only (strip body snippet)
              else first 3 words of attempt-0 query
  filter    = same as attempt 0
  top_k     = min(recall_k * 1.5, 50)   # optional bump, config flag

Attempt 2 (last resort) — only if still final_count == 0
  query     = section.title or section.section_id
  filter    = scope doc IDs only (drop category hard filter)
  categories for meta path = classification.categories + ["general"]
```

**Stop condition:** `len(reranked) > 0` OR attempts exhausted.

#### 3.1.2 Refactor (minimal)

Extract from `multi_retrieve_for_section`:

```python
async def _retrieve_attempt(
    client, *, tenant_id, section, query, categories,
    scope_document_ids, category_filter_enabled, settings, attempt_index,
) -> tuple[list[RetrievalHit], dict[str, Any]]:
    """One attempt: 3 paths → union → rerank. Returns hits + attempt meta."""
```

Outer loop in `multi_retrieve_for_section`:

```python
attempts_meta: list[dict] = []
hits: list[RetrievalHit] = []
for i in range(cfg.retrieval_max_attempts):
    query, filter_mode = _query_for_attempt(classification, section, i)
    hits, step = await _retrieve_attempt(..., attempt_index=i)
    attempts_meta.append(step)
    if step["final_count"] > 0:
        break
```

#### 3.1.3 New signature params (backward compatible)

```python
async def multi_retrieve_for_section(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    section: IndexedChunk,
    contract_type: str | None,
    policy_type: str | None,
    settings: ReviewSettings | None = None,
    classification: SectionCategoryResult | None = None,  # NEW — skip re-classify
    scope_document_ids: list[str] | None = None,            # NEW — from discovery
) -> SectionRetrievalBundle:
```

`final_verify_llm.py` keeps working (no scope needed for gap re-retrieve; optional pass `classification` if cached).

#### 3.1.4 Acceptance (P3.1)

- [ ] Seed policy matching only on **retry query** (title-only) → attempt 0 empty, attempt 1+ hits.
- [ ] Non-empty attempt 0 → exactly **1** attempt (no extra MCP calls).
- [ ] `retrieval_meta["attempts"]` length 1–3 with per-step counts.

---

### P3.2 — Typed clause map + category hard filter

#### 3.2.1 Batch classify (once per review)

**File:** `section_retrieval_nodes.py`

```python
classifications = await classify_all_sections(sections, contract_type=..., settings=...)
scope_ids = state.get("policy_document_ids") or state.get("discovered_policy_document_ids") or []

coros = [
    multi_retrieve_for_section(
        ...,
        classification=classifications.get(section.section_id),
        scope_document_ids=scope_ids,
    )
    for section in sections
]
```

Removes N redundant LLM classify calls → **production latency win**.

#### 3.2.2 Resolve category → document IDs

**Minimal MCP addition** (~25 lines): expose existing `list_policy_ids_by_categories` in document-mcp.

| File | Change |
|------|--------|
| `document_server/main.py` | `POST /tools/list_policy_ids_by_categories` |
| `review_agent/clients/document_client.py` | `list_policy_ids_by_categories(tenant_id, categories, contract_type)` |

Request body: `{ tenant_id, categories, contract_type? }` → `{ document_ids: string[] }`.

**Filter logic** (`multi_retrieval.py`):

```python
async def _resolve_search_scope(
    client, *, tenant_id, categories, contract_type, scope_document_ids, hard_filter: bool,
) -> tuple[list[UUID] | None, dict]:
    """
    Returns document_ids for SearchRequest, or None = full tenant (no filter).
    Intersect: category_ids ∩ scope_ids when both present.
    """
```

| Mode | `document_ids` on dense/FTS |
|------|----------------------------|
| Attempt 0 + hard filter ON | category ∩ scope (if non-empty) |
| Category filter → 0 IDs | meta `category_filter_skipped=true`; use scope only or full tenant per config |
| Attempt 2 | scope only (no category filter) |

Metadata path unchanged but uses same `document_ids` when filter active (already supported via `search_policy_by_categories` internals).

#### 3.2.3 Persist typed clause map on bundle

Extend `SectionRetrievalBundle` / `retrieval_meta` (no schema break — dict fields):

```python
retrieval_meta = {
    "categories": classification.categories,           # exists
    "query_terms": classification.query_terms,       # NEW
    "classify_warning": classification.classify_warning,
    "scope_document_ids": scope_document_ids,        # NEW
    "category_filter_document_ids": [...],           # NEW — resolved UUID strings
    "category_hard_filter": True,                      # NEW — whether applied
    "attempts": [...],                                 # P3.1 + P3.4
}
```

`bundle.categories` stays as today (compare/merge already reads it).

#### 3.2.4 Acceptance (P3.2)

- [ ] Liability section with `categories=["liability"]` → dense/FTS MCP requests include `document_ids` for liability-tagged policies only.
- [ ] Wrong-category policy **not** in union when filter active.
- [ ] Classifier runs **once** per review (mock: `classify_section_policies` call count = 0 from multi_retrieve when batch passed).

---

### P3.3 — Discovery cap: configurable + warn

**File:** `policy_discovery.py`

```python
cap = settings.discovery_max_policies
if cap <= 0:
    capped = ranked
else:
    capped = ranked[:cap]
    if settings.discovery_warn_on_cap and len(ranked) > len(capped):
        warnings.append(
            f"Policy discovery capped at {cap}; "
            f"{len(ranked) - len(capped)} policy(s) omitted (raise DISCOVERY_MAX_POLICIES or set 0 for unlimited)."
        )
```

**File:** `discovery_nodes.py` — pass cap stats into state (optional, 5 lines):

```python
"compliance_stats": {
    **state.get("compliance_stats") or {},
    "discovery_total_ranked": len(ranked),
    "discovery_returned": len(capped),
    "discovery_capped": len(ranked) > len(capped),
}
```

**Default:** keep `50`; prod tenants with 100+ playbooks set `DISCOVERY_MAX_POLICIES=0` or `200`.

#### Acceptance (P3.3)

- [ ] `discovery_max_policies=1` with 3 indexed policies → warning mentions omitted count.
- [ ] `discovery_max_policies=0` → all ranked policies returned, no cap warning.

---

### P3.4 — Per-section retry stats in `retrieval_meta`

#### 3.4.1 Canonical `retrieval_meta` shape

```json
{
  "categories": ["liability"],
  "query_terms": ["limitation of liability", "liability cap"],
  "scope_document_ids": ["uuid-1", "uuid-2"],
  "category_filter_document_ids": ["uuid-1"],
  "category_hard_filter": true,
  "final_attempt": 1,
  "final_count": 3,
  "attempts": [
    {
      "attempt": 0,
      "query": "limitation of liability twelve months",
      "category_hard_filter": true,
      "dense_count": 0,
      "fts_count": 0,
      "metadata_count": 1,
      "union_count": 1,
      "final_count": 0,
      "filter_document_count": 2
    },
    {
      "attempt": 1,
      "query": "Limitation of Liability",
      "category_hard_filter": true,
      "dense_count": 2,
      "fts_count": 1,
      "metadata_count": 1,
      "union_count": 3,
      "final_count": 3,
      "filter_document_count": 2
    }
  ]
}
```

Top-level `dense_count` / `fts_count` / … = **winning attempt** (backward compatible for `section_compare_nodes` path_counts).

#### 3.4.2 Aggregate ops stats

**File:** `section_retrieval_nodes.py`

```python
compliance_stats = {
    "sections_retrieved": len(bundles),
    "retrieval_path_hits": path_totals,           # existing
    "retrieval_retry_sections": sum(1 for b in bundles.values() if len(b.retrieval_meta.get("attempts", [])) > 1),
    "retrieval_zero_hit_sections": sum(1 for b in bundles.values() if not b.policy_hits),
    "retrieval_max_attempts_used": max(..., default=0),
}
```

**File:** `nodes.report_node` — already embeds `compliance_stats` in report metadata; no change required.

#### Acceptance (P3.4)

- [ ] Report JSON includes per-section `retrieval_meta.attempts` under serialized bundles in state (via existing `section_retrieval_by_id` in artifacts if exported).
- [ ] `compliance_stats.retrieval_retry_sections` > 0 when retry path used in test.

---

## 4. File change matrix

| File | Action | Tasks | ~Lines |
|------|--------|-------|--------|
| `review_agent/config.py` | Modify | P3.1, P3.2, P3.3 flags | +12 |
| `review_agent/services/multi_retrieval.py` | Modify | P3.1, P3.2, P3.4 | +120 |
| `review_agent/graph/section_retrieval_nodes.py` | Modify | P3.2 batch classify, scope ids, stats | +35 |
| `review_agent/services/policy_discovery.py` | Modify | P3.3 | +15 |
| `review_agent/graph/discovery_nodes.py` | Modify | P3.3 stats | +8 |
| `review_agent/clients/document_client.py` | Modify | P3.2 list IDs | +12 |
| `document_server/main.py` | Modify | P3.2 MCP tool | +18 |
| `review_agent/schemas/section_retrieval.py` | Optional docstring | P3.4 meta contract | +5 |
| `review_agent/.env.example` | Modify | docs | +6 |
| `review_agent/tests/test_multi_retrieval.py` | Modify | P3.1, P3.2 | +100 |
| `review_agent/tests/test_policy_discovery.py` | Modify | P3.3 | +25 |
| `review_agent/tests/test_section_retrieval_meta.py` | **Create** | P3.4 | +60 |

**Total:** ~400 lines (within 2–3 day estimate).

**No changes:** `section_compare_nodes`, `final_verify_llm` (except optional `classification` pass-through), `document_core/search.py` (reuse existing functions).

---

## 5. Implementation order

```text
Day 1
  1. config flags + .env.example
  2. MCP list_policy_ids_by_categories + client method
  3. _resolve_search_scope + category filter in _retrieve_attempt

Day 2
  4. Retry loop + attempts_meta (P3.1 + P3.4)
  5. section_retrieval_nodes: batch classify + scope_document_ids
  6. test_multi_retrieval retry + filter cases (mock client)

Day 3
  7. policy_discovery cap warning (P3.3)
  8. compliance_stats aggregates
  9. Integration test with Postgres: keyword-only policy found on retry
```

---

## 6. Test plan

### Unit (no Postgres)

| Test | Assert |
|------|--------|
| `test_retry_stops_on_first_hit` | 1 attempt, mock returns hits on attempt 0 |
| `test_retry_broadens_query` | attempt 0 query ≠ attempt 1 query |
| `test_category_filter_passes_document_ids` | mock dense search receives `document_ids` |
| `test_scope_intersection` | scope + category → intersected IDs in meta |
| `test_discovery_cap_warning` | 3 policies, cap=1 → warning string |

### Integration (Postgres + document-mcp)

| Scenario | Expected |
|----------|----------|
| Policy indexed with category `liability`; section classified liability | Hits on attempt 0 |
| FTS-only match (no dense overlap) | Found via union attempt 0 |
| Classifier returns obscure query; title matches policy | Retry attempt 1 succeeds |
| 60 policies discovered, cap=50 | Warning + 50 in `discovered_policy_document_ids` |

---

## 7. Retry vs “never miss” trade-offs

| Risk | Mitigation |
|------|------------|
| Category filter too strict → false empty | Attempt 2 drops category filter; meta logs `category_filter_skipped` |
| Retry adds latency | Early exit; max 3 attempts × 3 paths = 9 MCP calls **only when empty** |
| Classifier wrong category | Retry 2 searches full scope; gap LLM + final verify still catch misses |
| Discovery cap drops relevant policy | Warning + `discovery_capped` in stats; ops raises cap |

---

## 8. Explicit non-goals

- Fetching policies from external catalog on retry (Phase 2 ladder).
- Changing reranker or embedding model.
- Tombstone / stale policy exclusion (Sprint P2.3).
- New LangGraph nodes.

---

## 9. Definition of done

1. Empty retrieval triggers ≤2 retries with broadening; logs in `retrieval_meta.attempts`.
2. Dense + FTS respect category doc ID filter on attempt 0 when enabled.
3. Classifier runs once per review (batched), categories + query_terms on every bundle.
4. Discovery cap emits warning when truncated; `0` disables cap.
5. `compliance_stats` includes retry/zero-hit aggregates.
6. All existing `test_multi_retrieval.py` tests pass; new retry/filter tests added.

---

## 10. Flow after P3

```text
policy_discovery (cap + warn)
    → index_policies
    → classify_all_sections (batch, once)
    → for each section:
          multi_retrieve_for_section
            attempt 0: filtered dense+FTS+meta → union → rerank
            attempt 1–2 if empty: broaden query / drop filter
            → SectionRetrievalBundle + rich retrieval_meta
    → section_compare_llm (unchanged)
```

---

**Next (not P3):** Phase 2 catalog fetch on retry when tenant index empty; export `section_retrieval_by_id` summary to Java audit API.
