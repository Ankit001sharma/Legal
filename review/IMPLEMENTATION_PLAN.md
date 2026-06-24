# Contract Compliance Review Agent — Implementation Plan

**Document ID:** `IMPL-REVIEW-AGENT-v1.0`  
**Status:** Planning (no runtime code in this phase)  
**Last updated:** 2026-06-15  
**Related docs:**

- Architecture & product design: [`agent_details.md`](agent_details.md)
- Ingest technical spec: [`../mcp_contract_server/INGEST_SPEC.md`](../mcp_contract_server/INGEST_SPEC.md)
- Policy MCP spec: [`../mcp_policy_server/INGEST_SPEC.md`](../mcp_policy_server/INGEST_SPEC.md)
- Data models (scaffold only): [`schemas/chunk.py`](schemas/chunk.py), [`state/review_state.py`](state/review_state.py)

---

## Table of contents

1. [Purpose of this document](#1-purpose-of-this-document)
2. [Product goal](#2-product-goal)
3. [Core architectural decisions](#3-core-architectural-decisions)
4. [Problems we identified and agreed solutions](#4-problems-we-identified-and-agreed-solutions)
5. [Target system (what we are building)](#5-target-system-what-we-are-building)
6. [Scope boundaries](#6-scope-boundaries)
7. [Phased implementation plan](#7-phased-implementation-plan)
8. [Workstreams and ownership](#8-workstreams-and-ownership)
9. [Technology stack](#9-technology-stack)
10. [Deliverables checklist per phase](#10-deliverables-checklist-per-phase)
11. [Testing and acceptance strategy](#11-testing-and-acceptance-strategy)
12. [Risks and mitigations](#12-risks-and-mitigations)
13. [Timeline summary](#13-timeline-summary)
14. [What exists today vs what remains](#14-what-exists-today-vs-what-remains)

---

## 1. Purpose of this document

This is the **implementation plan only**. It does **not** contain production runtime code.

It records:

- What we agreed to build
- **Problems** the system will face (especially semantic fragmentation)
- **Solutions** we agreed on for each problem
- **Phased work** with tasks, dependencies, deliverables, and acceptance criteria
- How LangGraph, MCP, policy RAG, and own-model hosting fit together

Use this document to start sprints, estimate effort, and track progress.

---

## 2. Product goal

### 2.1 One-sentence goal

Enable a company to **upload policy documents**, send a **contract**, and receive a **grounded compliance report** comparing contract clauses against retrieved policy sections — **without per-company code or hardcoded company rules**.

### 2.2 User journey

```text
1. Company admin uploads policy PDFs/DOCX → indexed in policy RAG
2. Lawyer uploads contract (PDF/DOCX/text) or pastes text
3. System runs LangGraph review pipeline
4. System retrieves relevant policy sections per clause/dimension
5. System compares contract vs policy → PASS / FAIL / INCONCLUSIVE
6. System verifies quotes on both sides
7. Lawyer receives structured report (+ optional HITL approval)
```

### 2.3 Zero-config onboarding promise

| Zero-config (true) | System-level (still required) |
|--------------------|-------------------------------|
| No YAML per company | Generic review dimensions (what to search) |
| No custom code per tenant | Layout-aware ingest pipeline (same for all) |
| Rules live in uploaded policies | Compare logic, grounding, graph orchestration |
| Re-index policies → ready | Parser + parent–child index schema |

---

## 3. Core architectural decisions

These are **locked** for implementation unless explicitly revisited.

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Orchestration | **LangGraph** | Matches review flow, checkpoints, retry edges |
| Tools & RAG | **MCP servers** (contract, policy, legal later) | Swappable backends; agent stays thin |
| Policy source of truth | **Company-uploaded documents in RAG** | No hardcoded firm rules |
| Chunking | **Layout-aware + parent–child** (in MCP only) | Fixes semantic fragmentation |
| LLM | **Own model via OpenAI-compatible API (vLLM)** | No vendor lock-in for inference |
| Generic checklist | **`review_dimensions.yaml`** | Search intents only, not rules |
| Grounding | **Dual**: contract quote + policy quote | Fail closed if either ungrounded |
| LangGraph never chunks | **Ingest only in MCP** | Single place to fix parse quality |

---

## 4. Problems we identified and agreed solutions

This section captures the **discussion solutions** — implement these before scaling customers.

---

### Problem 1: Semantic fragmentation (orphaned clauses)

**Symptom:** Standard token chunking (e.g. 500 tokens) splits clauses mid-sentence. Section 10.2 liability lives in two unrelated vectors.

**Impact on our graph:**

| Node | Failure |
|------|---------|
| `clause_detection_node` | Misses or partially detects clause |
| `policy_retrieval_node` | Weak query from incomplete text |
| `compliance_review_node` | Compares fragment → wrong PASS/FAIL |
| `grounding_node` | May pass a misleading substring |

**Agreed solution:**

1. **Never** use token windows as primary chunk boundaries for contracts/policies.
2. Parse to **section tree** (`DocumentTree` / `SectionNode`).
3. **Parent chunk** = full numbered section including all subsections (e.g. entire `12.2` + `(a)(b)(c)`).
4. **Child chunk** = sentence/short paragraph **inside** parent — for search only.
5. LangGraph receives **parents only**.

**Implementation location:** `mcp_contract_server`, `mcp_policy_server` — see [`INGEST_SPEC.md`](../mcp_contract_server/INGEST_SPEC.md).

**Acceptance test:** A liability clause spanning a page break must appear as **one parent chunk**; search for liability returns that parent.

---

### Problem 2: Loss of hierarchical context (parent–child)

**Symptom:** Chunk contains only `(a) Except where such claims arise from Company's gross negligence` without knowing it belongs to `12.2 Indemnification` under `Section 12 IP`.

**Impact:** LLM invents what “such claims” means → false compliance findings.

**Agreed solution: Parent–child vector indexing**

```text
SEARCH:  embed child.context_text
         context_text = breadcrumb + parent title + child text

RETURN:  parent section full text to compliance reviewer
```

**Rules:**

- `search_contract` / `search_policy`: vector hit on child → resolve `parent_id` → dedupe → return parent.
- `compliance_review_node` **must not** receive `chunk_role=child` text.
- `get_section(section_id)` for exact fetch during grounding.

**Acceptance test:** Query matching subsection `(a)` returns parent text that includes `12.2 Indemnification` heading and full indemnity obligation.

---

### Problem 3: Structural variation across companies

**Symptom:** Company A uses Roman numerals, B uses Markdown `#`, C uploads messy PDF with TOC and footers.

**Impact:** Naive `\n\n` chunking indexes garbage → policy retriever returns wrong sections → false non-compliance.

**Agreed solution: Layout-aware parsing (markdown-first canonical form)**

| Format | Parser (priority) | Fallback |
|--------|-------------------|----------|
| PDF | Docling / layout model (self-hosted) | OCR + heuristic headings; flag `OCR_LOW_QUALITY` |
| DOCX | Heading styles → section tree | Paragraph merge |
| Raw text | Regex/heuristic headings | `structure_confidence=low` + report warning |

**Pipeline:**

```text
Upload → layout parse → canonical_text + section tree
      → parent/child chunks → embed → index
```

**Not in scope for zero-config:** Per-company chunk rules. Same pipeline for everyone.

**Acceptance test:** Three fixture policies (Roman, Markdown export, scanned PDF) all produce valid `section_path` and retrievable parent sections.

---

### Problem 4: RAG-only policy review is vague or ambiguous

**Symptom:** Policy says “adequate liability coverage” without numbers; LLM guesses thresholds.

**Agreed solution:**

- Compliance output must include status **`INSUFFICIENT_POLICY_CONTEXT`** when policy text cannot support a verdict.
- **Do not** invent FAIL/PASS when policy is vague.
- Optional: extract numeric amounts in a dedicated sub-step before compare (parsing, not company rules).

**Acceptance test:** Vague policy fixture → finding is `INSUFFICIENT_POLICY_CONTEXT`, not `NON_COMPLIANT`.

---

### Problem 5: Wrong policy document retrieved

**Symptom:** Security policy returned when procurement policy was needed.

**Agreed solution:**

- Index policies with metadata: `policy_type`, `applies_to_contract_types`, `effective_date`.
- `search_policy(query, filters={policy_type, contract_type})`.
- Return **multiple hits**; if policies conflict → `POLICY_CONFLICT` in report.

**Acceptance test:** Filtered search returns only vendor-management policy for MSA + liability query.

---

### Problem 6: Silent under-review (missed clauses)

**Symptom:** Clause detector misses liability → never retrieves liability policy → report looks clean.

**Agreed solution: Two-pass retrieval**

| Pass | Trigger | Action |
|------|---------|--------|
| **Clause-driven** | Detected clause type | `search_policy` + `search_contract` for that type |
| **Dimension-driven** | Generic `review_dimensions.yaml` | For each dimension, search even if detector missed |

**Acceptance test:** Contract with liability only in non-standard heading still gets liability dimension pass.

---

### Problem 7: Hallucinated quotes (grounding)

**Symptom:** Report cites “INR 50 lakhs” in policy but that number is not in the document.

**Agreed solution: Dual grounding gate**

```text
For each finding:
  verify_quote(contract) → must pass
  verify_quote(policy)   → must pass
  else → drop finding (fail closed)
```

Final report guard: no new claims in narrative that are not in `findings[]`.

**Acceptance test:** Injected fake quote in LLM output → dropped before report.

---

### Problem 8: Two agent stacks / OpenContracts coupling (from earlier review)

**Symptom:** Old `review_agent` folder mixed OpenContracts (Django) + PAKTON (LangGraph) + broken imports.

**Agreed solution for this project:**

- **New greenfield** under `review/` layout in this plan.
- **Do not** depend on `opencontractserver.*` or Django for MVP.
- **Do not** use PAKTON Archivist/Interrogator runtime; use as **pattern reference only**.
- Single stack: LangGraph + MCP + vLLM.

---

## 5. Target system (what we are building)

### 5.1 Component diagram

```text
                    ┌──────────────────┐
                    │  FastAPI (api/)  │
                    │  POST /review    │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │ LangGraph        │
                    │ (graph/)         │
                    └────────┬─────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
  mcp_contract_server   mcp_policy_server    llm_gateway
  (parse, search,       (index, search,      (your vLLM)
   ground)                ground)
         │                   │
         └─────────┬─────────┘
                   ▼
         PostgreSQL + pgvector
         MinIO (files)
         Redis (queue, optional)
```

### 5.2 LangGraph node flow (final)

```text
START
  → contract_parser_node       MCP: ingest_document
  → clause_detection_node      MCP: list_sections + LLM classify parents
  → policy_retrieval_node      MCP: search_policy + search_contract
  → compliance_review_node     LLM: compare parent sections
  → grounding_node             MCP: verify_quote × 2
  → report_node                Template + optional LLM narrative
  → END

Conditional edges:
  INSUFFICIENT_POLICY_CONTEXT → broader search_policy (max 2 retries)
  structure_confidence=low    → add warning to report
```

### 5.3 Folder structure (target end state)

```text
review/
├── review_agent/
│   ├── api/                 # FastAPI entry
│   ├── graph/               # LangGraph + nodes/
│   ├── agents/              # clause_detector, compliance_reviewer, grounding
│   ├── models/              # llm_gateway.py
│   ├── prompts/             # compliance, retrieval, report
│   ├── reports/             # report_generator + templates
│   ├── schemas/             # chunk.py (exists)
│   ├── state/               # review_state.py (exists)
│   ├── dimensions/          # review_dimensions.yaml (exists)
│   ├── config/
│   ├── agent_details.md
│   └── IMPLEMENTATION_PLAN.md   ← this file
├── mcp_contract_server/
│   ├── INGEST_SPEC.md       # exists
│   ├── tools.py             # stubs exist
│   ├── parser/              # PDF, DOCX, text
│   ├── indexer/             # parent-child builder
│   └── server.py            # MCP stdio/HTTP
├── mcp_policy_server/
│   ├── INGEST_SPEC.md
│   ├── tools.py
│   └── server.py
└── deploy/                  # docker-compose, vLLM, postgres
```

---

## 6. Scope boundaries

### 6.1 In scope (MVP → V1)

- PDF + DOCX + raw text contract ingest
- Policy upload + index (same pipeline)
- LangGraph compliance pipeline
- MCP contract + policy servers
- Own vLLM (OpenAI-compatible)
- Dual grounding
- Structured JSON report + markdown
- Generic review dimensions YAML
- Single-tenant or simple `tenant_id` column

### 6.2 Phase 2+

- HITL approval queue
- PDF export for lawyers
- `mcp_legal_server` (DPDP, GDPR statutes)
- Multi-tenant admin UI
- Celery for long documents + SSE progress
- Parallel dimension workers

### 6.3 Explicitly out of scope (do not build in MVP)

- Per-company YAML playbooks as rules source
- OpenContracts / Django integration
- PAKTON Archivist/Interrogator runtime
- Pinecone, Tavily, LlamaParse SaaS (unless emergency fallback)
- Token-based primary chunking
- Client-facing auto-release without lawyer review (configurable later)

---

## 7. Phased implementation plan

### Phase 0 — Foundation (Week 1)

**Goal:** Repo structure, configs, LLM gateway, schemas validated.

| Task | Owner | Deliverable |
|------|-------|-------------|
| Create `config/` settings (vLLM URL, DB, MinIO) | Backend | `.env.example` |
| Implement `models/llm_gateway.py` | Backend | Single entry for all LLM calls |
| Move prompts to `prompts/` from `prompts.py` | Backend | `compliance.py`, `retrieval.py` |
| Validate `schemas/chunk.py` + `state/review_state.py` | Backend | Unit tests for models |
| Docker-compose skeleton: Postgres+pgvector, MinIO, vLLM | DevOps | `deploy/docker-compose.yml` |

**Exit criteria:**

- [ ] `llm_gateway.chat()` returns response from your vLLM
- [ ] Postgres + pgvector container starts
- [ ] Schemas import without errors

---

### Phase 1 — MCP contract ingest (Weeks 2–3)

**Goal:** Layout-aware parse + parent–child index (solves Problems 1–3).

| Task | Owner | Deliverable |
|------|-------|-------------|
| PDF parser wrapper (Docling REST or local) | Ingest | `parser/pdf_parser.py` |
| DOCX structural parser | Ingest | `parser/docx_parser.py` |
| Text heuristic parser | Ingest | `parser/text_parser.py` |
| Section tree builder | Ingest | `indexer/section_tree.py` |
| Parent–child chunk builder | Ingest | `indexer/parent_child.py` |
| pgvector store + SQL migrations | Ingest | Tables per INGEST_SPEC |
| Embedder service (sentence-transformers / your model) | ML | HTTP or in-process |
| Implement `ingest_document` | Ingest | End-to-end index |
| Implement `list_sections`, `get_section` | Ingest | Parent enumeration |
| Structure confidence + warnings | Ingest | `IngestResult.warnings` |

**Exit criteria:**

- [ ] 50-page PDF → parent sections, no mid-clause splits
- [ ] Subsection `(a)` search returns full parent `12.2`
- [ ] DOCX + text fixtures pass
- [ ] `structure_confidence` set correctly

**Dependencies:** Phase 0 DB + embedder

---

### Phase 2 — MCP retrieval + grounding (Week 4)

**Goal:** Search children, return parents; quote verification (Problems 2, 7).

| Task | Owner | Deliverable |
|------|-------|-------------|
| Implement `search_contract` (child → parent) | Ingest | `RetrievalHit[]` |
| Optional reranker (local cross-encoder) | ML | Top-k refinement |
| Implement `verify_quote` | Ingest | `GroundingCheckResult` |
| MCP server transport (stdio or HTTP) | Backend | `server.py` |
| Golden fixtures: 5 contracts | QA | `tests/fixtures/contracts/` |

**Exit criteria:**

- [ ] `return_parents_only=true` never returns child-only bodies to caller
- [ ] Grounding rejects quotes not in `canonical_text`
- [ ] MCP tools callable from Python test client

---

### Phase 3 — MCP policy server (Week 5)

**Goal:** Policy upload + filtered search (Problems 4–5).

| Task | Owner | Deliverable |
|------|-------|-------------|
| Copy ingest pipeline with `kind=policy` | Ingest | `mcp_policy_server/` |
| Policy metadata on index (`policy_type`, etc.) | Ingest | Index fields |
| `search_policy` with filters | Ingest | Tenant + type filters |
| `index_policy` upload API | Backend | Admin endpoint or MCP tool |
| Policy fixtures (3 doc types) | QA | `tests/fixtures/policies/` |

**Exit criteria:**

- [ ] Upload vendor policy → search “liability” returns correct section
- [ ] Filter by `contract_type=msa` works
- [ ] Conflicting policies return multiple hits (data for `POLICY_CONFLICT`)

---

### Phase 4 — LangGraph pipeline (Weeks 6–7)

**Goal:** End-to-end review graph (Problem 6 two-pass).

| Task | Owner | Deliverable |
|------|-------|-------------|
| `graph/review_graph.py` | Agent | Compiled StateGraph |
| `contract_parser_node` | Agent | Calls `ingest_document` |
| `clause_detection_node` | Agent | `list_sections` + LLM types |
| `policy_retrieval_node` | Agent | Clause + dimension passes |
| `compliance_review_node` | Agent | PASS/FAIL/INSUFFICIENT |
| `grounding_node` | Agent | Dual verify, drop bad findings |
| `report_node` | Agent | `ReviewReport` JSON + markdown |
| Retry edge for insufficient context | Agent | Max 2 RAG retries |
| Load `review_dimensions.yaml` | Agent | Dimension-driven pass |

**Exit criteria:**

- [ ] End-to-end: contract file + indexed policies → report JSON
- [ ] Vague policy → `INSUFFICIENT_POLICY_CONTEXT`
- [ ] Ungrounded finding dropped
- [ ] Dimension pass catches clause detector miss (fixture)

---

### Phase 5 — API + reports (Week 8)

**Goal:** External entry point and readable output.

| Task | Owner | Deliverable |
|------|-------|-------------|
| `api/review_api.py` — `POST /review` | Backend | Async job or sync MVP |
| `reports/report_generator.py` | Backend | Markdown from `ReviewReport` |
| `reports/templates/compliance_report.md` | Backend | Lawyer-readable layout |
| Health checks | Backend | `GET /health` |
| Error responses (low structure, OCR fail) | Backend | HTTP + report warnings |

**Exit criteria:**

- [ ] API accepts PDF upload + `tenant_id`
- [ ] Returns `report_id` + full JSON + markdown
- [ ] Errors are actionable (encrypted PDF, empty file)

---

### Phase 6 — Hardening & pilot (Weeks 9–10)

**Goal:** Production pilot readiness.

| Task | Owner | Deliverable |
|------|-------|-------------|
| Celery + Redis for long jobs | Backend | Optional async |
| SSE or polling for job status | Backend | Progress events |
| Audit log (tool calls, model route) | Backend | `audit_events` table |
| Golden eval suite (20 contracts) | QA | CI gate |
| Load test 50-page MSA | QA | P95 latency documented |
| Security: tenant isolation in RAG | Backend | Queries always filter `tenant_id` |
| Documentation runbook | DevOps | Deploy + rollback |

**Exit criteria:**

- [ ] 20 golden tests pass in CI
- [ ] Grounding pass rate ≥ 95% on fixtures
- [ ] Pilot with 3–5 real policy PDFs + contracts successful

---

## 8. Workstreams and ownership

| Workstream | Responsibility | Key files |
|------------|----------------|-----------|
| **Ingest & MCP** | Parse, chunk, index, search, ground | `mcp_*_server/` |
| **Agent & Graph** | LangGraph nodes, compliance logic | `graph/`, `agents/` |
| **LLM & prompts** | Gateway, prompts, eval | `models/`, `prompts/` |
| **API & reports** | HTTP, templates | `api/`, `reports/` |
| **Platform** | DB, vLLM, Docker, CI | `deploy/` |

Recommended team: **2 developers** (Ingest+MCP | Graph+API) + part-time QA for fixtures.

---

## 9. Technology stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Orchestration | LangGraph | Checkpointer in Phase 6 |
| API | FastAPI | |
| LLM | vLLM (OpenAI-compatible) | Your own weights |
| Embeddings | Self-hosted sentence-transformers / legal embed model | |
| Vector DB | PostgreSQL + pgvector | |
| Object storage | MinIO | PDF/DOCX blobs |
| Queue (Phase 6) | Redis + Celery | |
| MCP transport | stdio (dev) → HTTP (prod) | |
| Parsers | Docling (PDF), python-docx (DOCX) | Self-host, no SaaS |

**External SaaS (default: off):**

| Service | MVP |
|---------|-----|
| OpenAI / Anthropic | No — use vLLM |
| Pinecone | No — pgvector |
| Cohere rerank | No — local reranker or skip |
| Tavily / web | No |
| LangSmith | Optional dev only |

---

## 10. Deliverables checklist per phase

| Phase | Must ship |
|-------|-----------|
| 0 | llm_gateway, docker-compose, env example |
| 1 | `ingest_document`, parent–child index, 3 parsers |
| 2 | `search_contract`, `verify_quote`, MCP server |
| 3 | `mcp_policy_server` full parity + metadata |
| 4 | Full LangGraph + dimension two-pass |
| 5 | `POST /review` + markdown report |
| 6 | CI golden tests, tenant isolation, pilot |

---

## 11. Testing and acceptance strategy

### 11.1 Fixture categories

| Category | Count (min) | Purpose |
|----------|-------------|---------|
| Contracts PDF | 10 | Orphan clause, long MSA, scanned |
| Contracts DOCX | 5 | Heading structure |
| Policies | 10 | Roman / Markdown / messy PDF |
| Vague policy | 3 | INSUFFICIENT_POLICY_CONTEXT |
| Numeric mismatch | 5 | INR/USD amount compare |
| Adversarial grounding | 5 | Fake quotes must drop |

### 11.2 CI gates (Phase 6)

| Metric | Threshold |
|--------|-----------|
| Parent retrieval on subsection query | 100% on fixture set |
| Grounding precision | ≥ 95% |
| False CRITICAL rate | < 5% vs lawyer labels |
| Ingest P95 (50-page PDF) | < 120s |

---

## 12. Risks and mitigations

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Docling fails on scans | High | OCR path + `OCR_LOW_QUALITY` warning |
| Policy docs vague | High | `INSUFFICIENT_POLICY_CONTEXT` status |
| vLLM quality insufficient | Medium | Eval early Phase 0; fallback model slot in gateway |
| MCP latency | Medium | Batch retrieval; cache playbook sections per tenant |
| Scope creep (HITL, statutes) | Medium | Phase gates in this doc |
| Re-building OpenContracts | Low | Explicit out-of-scope |

---

## 13. Timeline summary

| Team | MVP (Phases 0–5) | Production pilot (Phase 6) |
|------|------------------|----------------------------|
| **1 developer** | ~8 weeks | ~10 weeks |
| **2 developers** | ~5–6 weeks | ~7–8 weeks |

**Critical path:** Phase 1 ingest (parent–child) → Phase 2 search → Phase 4 graph.

Do **not** start LangGraph compliance logic until Phase 2 search returns correct parents.

---

## 14. What exists today vs what remains

### Exists today (planning scaffold only)

| Artifact | Type | Notes |
|----------|------|-------|
| `agent_details.md` | Product/architecture doc | |
| `IMPLEMENTATION_PLAN.md` | This plan | |
| `schemas/chunk.py` | Data models | Not wired to DB |
| `state/review_state.py` | Graph state types | No graph yet |
| `dimensions/review_dimensions.yaml` | Search checklist | |
| `mcp_contract_server/INGEST_SPEC.md` | Technical spec | |
| `mcp_contract_server/tools.py` | Stubs | `NotImplementedError` |
| `mcp_policy_server/INGEST_SPEC.md` | Technical spec | |
| `prompts.py` | Legacy prompts | Move to `prompts/` in Phase 0 |
| `builder.py` | Old Interrogator graph | **Do not use** — reference only |

### Remains to implement (all phases above)

- All parsers, indexers, DB, MCP servers (real)
- LangGraph nodes and graph
- `llm_gateway`, API, reports
- Tests, deploy, CI

---

## Appendix A — Problem → solution quick reference

| # | Problem | Solution | Phase |
|---|---------|----------|-------|
| 1 | Orphaned clauses | Section-level parent chunks | 1 |
| 2 | Lost hierarchy | Parent–child index; return parents | 1–2 |
| 3 | Format variation | Layout-aware parsers | 1 |
| 4 | Vague policies | `INSUFFICIENT_POLICY_CONTEXT` | 4 |
| 5 | Wrong policy doc | Metadata + filtered search | 3 |
| 6 | Missed clauses | Dimension two-pass | 4 |
| 7 | Fake quotes | Dual grounding; fail closed | 2, 4 |
| 8 | Old stack coupling | Greenfield; no OpenContracts | 0 |

---

## Appendix B — Sprint 1 starter tasks (when implementation begins)

1. Phase 0: `llm_gateway` + docker-compose  
2. Phase 1: PDF → `DocumentTree` → one parent chunk in memory (no DB yet)  
3. Phase 1: Parent–child builder unit test with fixture `12.2(a)`  
4. Phase 2: `search` returns parent for child hit  
5. Phase 4: Single-node graph `ingest → list_sections → END`  

---

*End of implementation plan. No production runtime code is implied by this document until phases are explicitly started.*
