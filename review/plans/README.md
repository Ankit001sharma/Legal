# Dynamic Review — Implementation Plans

Three phased plans to replace static YAML-driven review with **production-grade dynamic policy review**.

| Plan | ID | Status | Depends on |
|------|-----|--------|------------|
| **Phase 1** — Dynamic Review Plan | `DR-PHASE-1` | Implemented |
| **Phase 2** — Policy Fetch & Retry | `DR-PHASE-2` | Implemented |
| [Phase 3 — Prompt Split (LLM Filter)](./PHASE3_PROMPT_SPLIT_PLAN.md) | `DR-PHASE-3` | Implemented |
| [Phase 4 — Persistent RAG Store](./PHASE4_PERSISTENT_RAG_STORE_PLAN.md) | `DR-PHASE-4` | Partial (4A–4C core) |
| [Phase 5 — Hybrid Batch Compliance](./PHASE5_HYBRID_COMPLIANCE_PLAN.md) | `DR-PHASE-5` | Implemented (core) |
| [Phase 6 — Contract-First Discovery](./PHASE6_CONTRACT_FIRST_DISCOVERY_PLAN.md) | `DR-PHASE-6` | Implemented (core) |
| [Phase 6B — Output Polish & Prod Defaults](./PHASE6B_OUTPUT_POLISH_PLAN.md) | `DR-PHASE-6B` | Implemented |
| [Phase 7 — Java Catalog Integration](./PHASE7_JAVA_CATALOG_INTEGRATION_PLAN.md) | `DR-PHASE-7` | Implemented |
| [Java Catalog API Contract](./JAVA_CATALOG_API_CONTRACT.md) | — | Spec |
| [Phase 9 — Postgres Session & Memory](../legal_ai_platform/docs/PHASE9_POSTGRES_SESSION_MEMORY_PLAN.md) | `DR-PHASE-9` | Implemented |
| [Phase 10 — Section-First + High-Recall Retrieval](./PHASE10_SECTION_FIRST_RETRIEVAL_PLAN.md) | `DR-PHASE-10` | v1 shipped |
| [Phase 10 — Production unified (single pipeline)](./PHASE10_PRODUCTION_UNIFIED_IMPL_PLAN.md) | `DR-PHASE-10-PROD` | **Execute next** |

## Phase 10 (accuracy — production cutover)

Section-first LLM review + multi-path retrieval. v1 dual-mode shipped; **next:** [Production unified plan](./PHASE10_PRODUCTION_UNIFIED_IMPL_PLAN.md) — one pipeline, remove legacy, no fallbacks.

## Phase 9 (done — session & memory)

## Phase 8 (prod ingest)

PDF/contract-by-ID, Java sync policies — see prior summary.

## Phase 7 (done)

## Phase 6B (done)

Policy title on violations, sharper compare/routing prompts, `.env.production.example`.

## Phase 6 (done — contract only)

User sends **contract only** with `REVIEW_POLICY_SOURCE=tenant_auto` → LLM/lexical routing → discover policies from tenant index → hybrid compare.

## Phase 6 (product enablement)

Set `REVIEW_POLICY_SOURCE=tenant_auto` + `COMPLIANCE_MODE=hybrid` in production after QA.

## Phase 5 (done)

Hybrid align → prescreen → batched LLM Pass 1 → gap retrieve → Pass 2.

## Phase 4 shipped (core)

- `PgVectorDocumentStore` + SQL migration (`DOCUMENT_STORE_BACKEND=pgvector`)
- Hybrid search hook (`SEARCH_BACKEND=hybrid`, optional embeddings)
- `REVIEW_POLICY_SCOPE=request` (default) — only request-scoped policies reviewed
- Orchestrator accepts `policy_refs` / `policy_document_ids` without inline `policies[]`

## Problem (one line)

Rules live in **tenant policy documents**; review categories and retrieval are **dynamic** (Phase 1–2 done). Phase 3 LLM filter optional.

## Code facts (verified)

- Dynamic plan: `policy_plan_node` + `build_review_plan()`
- Retrieval ladder: `resolve_policy_hits()` — exact → search → catalog fetch
- Catalog: `StubPolicyCatalogClient` / `HttpPolicyCatalogClient` via `POLICY_CATALOG_URL`
- LLM filter: `filter_categories_llm()` via `REVIEW_PLAN_LLM_FILTER` (default off)

```text
load_memory → index_policies → contract_parser → clause_detection
  → policy_plan (dynamic categories)
  → policy_retrieval (get_section + search + fetch/retry)
  → compliance_review → grounding → report → save_memory
```

## Agreed defaults

| Decision | Value |
|----------|-------|
| Category granularity | One review unit per **policy parent section** |
| Max categories | 30 (`REVIEW_MAX_CATEGORIES`), warn when capped |
| `policy_refs` | Opaque `list[str]`; catalog client resolves |
| LLM category filter | Off by default (`review_plan_llm_filter=false`) |
| Policy scope | `request` (default) or `tenant` (`REVIEW_POLICY_SCOPE`) |
| Document store | `memory` (default) or `pgvector` (`DOCUMENT_STORE_BACKEND`) |
| Empty policy store | Valid report + warning, no hard fail |
| `ComplianceFinding.dimension_id` | **Keep field name**; value = `category.category_id` |
| E2E tests | Dynamic mode default; `static` opt-in via env |

## Implementation order

1. **Phase 1** — Done
2. **Phase 2** — Done
3. **Phase 3** — Done (enable `REVIEW_PLAN_LLM_FILTER=true` per tenant when needed)
4. **Phase 4** — Partial (pgvector + scope; finish 4D hardening as needed)
5. **Phase 5** — Done ([hybrid batch compliance](./PHASE5_HYBRID_COMPLIANCE_PLAN.md))
6. **Phase 6** — Done ([contract-first discovery](./PHASE6_CONTRACT_FIRST_DISCOVERY_PLAN.md))
7. **Phase 6B** — Done ([output polish](./PHASE6B_OUTPUT_POLISH_PLAN.md))
8. **Phase 7** — Done ([Java catalog integration](./PHASE7_JAVA_CATALOG_INTEGRATION_PLAN.md))
9. **Phase 9** — Done ([Postgres session & memory](../legal_ai_platform/docs/PHASE9_POSTGRES_SESSION_MEMORY_PLAN.md))
10. **Phase 10** — Planned ([Section-first + high-recall retrieval](./PHASE10_SECTION_FIRST_RETRIEVAL_PLAN.md))
