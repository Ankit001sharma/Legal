# Sprint 4 — Playbook Judgment + Conflicts + Grounding (P4.1–P4.4) — REVISED

**Plan ID:** `DR-PHASE-13-P4-R2`  
**Scope:** Review agent Python only  
**Goal:** Harvey/Ironclad-style **dynamic playbook** review — compare contract sections to **tenant-indexed policy text** with citation-locked LLM; explicit conflicts; no silent loss after grounding.  
**Depends on:** Phase 10 section-first pipeline, 10C final gap verify, Phase 12 P3 retrieval  
**Estimate:** ~280 lines prod code, ~200 lines tests, **4–5 days**  
**Explicitly excluded:** YAML/regex rule engines, fixed legal thresholds in repo, skip-LLM deterministic paths, NLI/GraphRAG (later)

---

## 0. Why this revision (no deterministic rules)

Policies are **dynamic per tenant** (Java sync → document-mcp → pgvector). Playbook content lives in **indexed policy text + registry metadata**, not in Python constants.

| Wrong (removed) | Right (this plan) |
|-----------------|-------------------|
| `compliance_rules.yaml` with liability/notice thresholds | Retrieved **policy section text** is the playbook |
| Regex “unlimited liability” in repo | LLM compares contract vs **that tenant’s** policy chunks |
| Skip LLM when rule fires | LLM compare always runs when policy hits exist; enrichment makes it **playbook-aware** |
| Vendor-owned legal logic | **Customer-owned** positions via sync metadata + indexed text |

---

## 1. How production products handle dynamic playbooks

### Harvey ([contract review / playbooks](https://www.harvey.ai/blog/how-ai-is-transforming-contract-review-software))

- Customer **uploads or builds playbooks** (Word, Vault, Agent Builder) — content is **tenant data**, not Harvey source code.
- **Agentic workflow:** ingest → classify → **retrieve playbook + contract context** → structured issues → memo/redlines.
- **Grounding:** findings tied to source documents; human accepts/rejects.
- **No** global regex rule file in the product binary for “30-day notice”.

### Ironclad ([AI Playbooks](https://support.ironcladapp.com/hc/en-us/articles/12275685560215-Ironclad-AI-Playbooks-Overview))

- Per-workflow **preferred / fallback / non-standard** language stored in **playbook config** (database).
- AI **detects clause** → compares document text to **configured positions** → flags deviation → optional precise redline.
- Positions change per customer and contract type — **dynamic data**, LLM-assisted matching.

### ContractKen-style compound AI

- Parse → classify → **RAG over clause library + playbooks** → **LLM compare with mandatory citations** → post-process.
- Judgment = **retrieved institutional text + structured LLM**, not hardcoded rules.

### Mapping to your architecture

| Industry pattern | Your equivalent (existing / P4) |
|------------------|----------------------------------|
| Playbook repository | Tenant policies in pgvector + `policy_documents` registry |
| Sync / CMS | Java → `register_policy` + `index_policy` |
| Clause / section structure | Section-first contract parser + `list_sections` |
| Retrieve relevant playbook | P3 `multi_retrieval` + discovery |
| Deviation analysis | `section_compare_llm` (structured output) |
| Quote verification | `quote_validate` + document-mcp `verify_quote` |
| Conflict / escalation | **P4.3** `POLICY_CONFLICT` |
| No silent drop | **P4.4** grounding downgrade + coverage |

**Best approach for you:** **Playbook-native RAG + citation-locked structured LLM** — same class as Harvey/Ironclad, aligned with Java sync and dynamic policies. **Do not add a rule engine.**

---

## 2. Problem statement (verified in code)

| ID | Gap | Current code |
|----|-----|--------------|
| P4.1 | Compare uses retrieved policy **body only**; registry **position hints** (preferred/fallback/guidance) from Java not passed to LLM | `_format_sections_block` in `section_compare_llm.py` L86–95; `RegisterPolicyRequest.metadata` exists but unused in compare |
| P4.2 | Findings lack playbook traceability (`metadata.source`, policy ref, position type) | `section_merge.py` sets `compliance_mode` only |
| P4.3 | Re-compare can leave **conflicting statuses** without `POLICY_CONFLICT` row | `final_verify_llm.py` L362–393 supersedes on success only |
| P4.4 | Grounding **drops** findings | `grounding_node` in `nodes.py` L248–253; coverage runs **before** grounding |

**Already strong (keep):**

- Section-first graph, tenant_auto discovery, P3 retry retrieval  
- Structured `SectionCompareItem`, quote downgrade in compare  
- `ComplianceStatus.POLICY_CONFLICT` enum exists  
- `ensure_section_coverage()` for backfill  

---

## 3. Design principles

1. **Playbook = indexed policy text** — primary evidence is retrieved parent chunks; metadata is **hints**, not replacement for text.  
2. **LLM is the judgment engine** — constrained by schema + quote validation + grounding (Harvey model).  
3. **Dynamic only** — preferred/fallback strings come from `register_policy.metadata` or catalog sync, never from repo YAML.  
4. **Minimal graph change** — no new nodes; enrich compare + fix verify/grounding.  
5. **Never silently wrong** — conflicts explicit; grounding downgrades, not deletes.

---

## 4. Target pipeline (after P4 — unchanged topology)

```text
section_policy_retrieval     (P3 — retrieve tenant playbook sections)
    → section_compare_llm      (P4.1/P4.2 — playbook-enriched prompt)
    → merge_section_findings
    → final_gap_verify         (P4.3 — emit POLICY_CONFLICT if unresolved)
    → grounding                (P4.4 — downgrade not drop)
    → post_grounding_coverage  (P4.4 — inline in grounding_node)
    → report
```

---

## 5. Java / metadata contract (dynamic playbook hints)

Java (or catalog) may attach **optional** fields on `register_policy` / ingest `metadata` (no Python defaults required):

```json
{
  "categories": ["liability"],
  "review_guidance": "Cap should align with fees paid in prior 12 months.",
  "preferred_position": "Optional verbatim preferred clause text...",
  "fallback_positions": [
    {"label": "6-month cap", "text": "..."}
  ],
  "position_type": "standard"
}
```

Python **reads only** — never authors legal content. If fields absent, compare uses retrieved chunk text alone (today’s behavior).

---

## 6. Task breakdown

### P4.1 — Playbook context builder (dynamic metadata)

#### 6.1.1 New module

**File:** `review_agent/services/playbook_context.py` (~70 lines)

```python
def build_playbook_hints_by_document(
    indexed_policies: list[dict],
    *,
    registry_records: list[PolicyRegistryRecord] | None = None,
) -> dict[str, PlaybookHints]:
    """
    Map policy document_id → hints from indexed_policies + registry metadata.
    Keys: review_guidance, preferred_position, fallback_positions, policy_ref, title.
    """

def format_playbook_hint_block(hints: PlaybookHints | None) -> str:
    """Markdown block appended under Policy N in compare prompt (empty if no hints)."""
```

**Data sources (in order):**

1. `state.indexed_policies[]` from discovery (already on state)  
2. Optional: batch `list_policy_registry` MCP call once per review if `PLAYBOOK_LOAD_REGISTRY=true` (cache by `document_id`)

No MCP change required for v1 if metadata is copied onto `indexed_policies` during discovery/index_policies.

#### 6.1.2 Wire hints into compare prompt

**File:** `section_compare_llm.py` — extend `_format_sections_block`:

```text
- **Policy 1** doc=... section=... title=...
  Playbook hints: ref=vendor-msa-liability | guidance: ...
  Preferred position (if any): ```...```
  ```<retrieved section text>```
```

**File:** `section_compare_nodes.py` — pass `indexed_policies` from state into `compare_all_sections` / `compare_section_batch`.

#### 6.1.3 Acceptance (P4.1)

- [ ] Policy with `metadata.review_guidance` in registry → hint block appears in LLM user prompt.  
- [ ] Policy without metadata → identical to current prompt (no regression).  
- [ ] Unit test: `format_playbook_hint_block` with/without fields.

---

### P4.2 — Playbook-grounded findings metadata

#### 6.2.1 Enrich findings at merge

**File:** `section_merge.py` — `section_items_to_findings`:

```python
metadata={
    "compliance_mode": pipeline,
    "source": "playbook_compare",
    "confidence": item.confidence,
    "policy_ref": hints.policy_ref if hints else None,
    "playbook_guidance_used": bool(hints and hints.review_guidance),
}
```

Lookup hints by `policy_document_id` from `playbook_context` map passed into merge.

**File:** `merge_section_findings_node` — build hints once from state, pass to merge.

#### 6.2.2 Prompt tweak (minimal)

**File:** `prompts/section_compare.md` — add 5–8 lines under SYSTEM:

- When **Playbook hints** include `preferred_position`, treat it as the organization’s target; deviation → `NON_COMPLIANT` or `INCONCLUSIVE` with both quotes from contract and retrieved policy text.  
- When two retrieved policies disagree, use `POLICY_CONFLICT`.  
- **Do not** invent policy requirements not present in retrieved text or hints.

#### 6.2.3 Config

| Variable | Default | Purpose |
|----------|---------|---------|
| `PLAYBOOK_ENRICH_COMPARE` | `true` | P4.1 hint blocks |
| `PLAYBOOK_LOAD_REGISTRY` | `false` | Optional registry MCP fetch per review |

#### 6.2.4 Acceptance (P4.2)

- [ ] Report findings include `metadata.source=playbook_compare`.  
- [ ] When Java sends `preferred_position`, LLM rationale references deviation (integration test with mock LLM or recorded fixture).  
- [ ] No new graph nodes; compare still runs for all sections with policy hits.

---

### P4.3 — Explicit `POLICY_CONFLICT` after unresolved re-compare

*(Same intent as prior plan; no deterministic logic.)*

#### 6.3.1 New helper

**File:** `review_agent/services/conflict_resolve.py` (~55 lines)

```python
def emit_unresolved_policy_conflict(
    section_id: str,
    prior_findings: list[ComplianceFinding],
    new_findings: list[ComplianceFinding],
) -> ComplianceFinding | None:
    """
    If material statuses still conflict (COMPLIANT vs NON_COMPLIANT, or multiple
    NON_COMPLIANT with incompatible rationales), return one POLICY_CONFLICT finding.
    """
```

**Output:**

- `status=POLICY_CONFLICT`, `severity=critical`  
- `policy_quote` = concatenated verbatim quotes from conflicting policy findings (cap 2000 chars)  
- `metadata.source=conflict_resolver`, `metadata.conflict_finding_ids=[...]`

#### 6.3.2 Wire in `final_verify_llm.py`

After conflict re-compare for section `sid`:

- If `emit_unresolved_policy_conflict(...)` returns a row → append it, supersede all conflicting IDs.  
- If re-compare **resolves** to one status → keep new findings only (today’s behavior).  
- If re-compare **skipped** (no hits) → optionally emit `POLICY_CONFLICT` with rationale “conflict could not be re-evaluated” (config `CONFLICT_EMIT_ON_SKIP=false` default).

#### 6.3.3 Stats + report

- `final_verify_stats.conflicts_unresolved`  
- `compliance_stats.policy_conflict_count` in report metadata  

#### 6.3.4 Acceptance (P4.3)

- [ ] Mock re-compare still returning mixed statuses → exactly one `POLICY_CONFLICT` in merged findings.  
- [ ] Resolved re-compare → no `POLICY_CONFLICT`.  
- [ ] Report generator renders `POLICY_CONFLICT` (verify `reports/generator.py`).

---

### P4.4 — Grounding: downgrade + post-grounding coverage

*(No deterministic rules — safety layer only.)*

#### 6.4.1 `grounding_node` changes (`nodes.py`)

Replace drop branch:

```python
if ok:
    grounded.append(finding.model_copy(update={"grounded": True}))
elif settings.grounding_downgrade_not_drop:
    grounded.append(finding.model_copy(update={
        "status": ComplianceStatus.INCONCLUSIVE,
        "grounded": False,
        "metadata": {**finding.metadata, "grounding_failed": True, "prior_status": finding.status.value},
        # strip failed quotes
    }))
else:
    # legacy drop when flag false
```

Track `contract_ok` / `policy_ok` separately.

#### 6.4.2 Post-grounding coverage

End of `grounding_node` when `GROUNDING_RERUN_COVERAGE=true`:

```python
coverage = ensure_section_coverage(reviewable_sections(...), grounded, ...)
return {"grounded_findings": coverage.findings, "section_coverage": {...}}
```

Fixes: coverage today runs in `final_gap_verify_node` **before** grounding can drop rows.

#### 6.4.3 Config

| Variable | Default |
|----------|---------|
| `GROUNDING_DOWNGRADE_NOT_DROP` | `true` |
| `GROUNDING_RERUN_COVERAGE` | `true` |

#### 6.4.4 Acceptance (P4.4)

- [ ] Bad contract quote → finding stays as `INCONCLUSIVE`, section still in report.  
- [ ] Section would become uncovered after drop → backfill via coverage.  
- [ ] `metadata.grounding_failed=true` present on downgraded rows.

---

## 7. File change matrix

| File | Action | Task | ~Lines |
|------|--------|------|--------|
| `services/playbook_context.py` | **Create** | P4.1 | 70 |
| `services/section_compare_llm.py` | Modify | P4.1 prompt blocks | 25 |
| `graph/section_compare_nodes.py` | Modify | P4.1 pass indexed_policies | 12 |
| `services/section_merge.py` | Modify | P4.2 metadata | 15 |
| `graph/section_compare_nodes.py` | Modify | P4.2 merge hints | 8 |
| `prompts/section_compare.md` | Modify | P4.2 guidance | 10 |
| `services/conflict_resolve.py` | **Create** | P4.3 | 55 |
| `services/final_verify_llm.py` | Modify | P4.3 wire | 25 |
| `graph/nodes.py` | Modify | P4.4 grounding + coverage | 45 |
| `config.py` | Modify | flags | 8 |
| `.env.example` | Modify | docs | 8 |
| `clients/document_client.py` | Modify (optional) | P4.1 registry list | 15 |
| `tests/test_playbook_context.py` | **Create** | P4.1 | 60 |
| `tests/test_conflict_resolve.py` | **Create** | P4.3 | 70 |
| `tests/test_grounding_downgrade.py` | **Create** | P4.4 | 80 |

**Total:** ~480 lines (incl. tests). **Zero** rule YAML, **zero** new graph nodes.

---

## 8. Implementation order

```text
Day 1 — P4.1
  playbook_context.py + _format_sections_block + tests

Day 2 — P4.2
  merge metadata + prompt tweak + section_compare_nodes wiring

Day 3 — P4.3
  conflict_resolve.py + final_verify_llm + tests

Day 4 — P4.4
  grounding_node downgrade + post-grounding coverage + tests

Day 5 — Integration smoke
  review with indexed policy + metadata.review_guidance
  conflict scenario + grounding downgrade in report JSON
```

---

## 9. Test plan

| Layer | Tests |
|-------|--------|
| Unit | `test_playbook_context.py`, `test_conflict_resolve.py`, `test_grounding_downgrade.py` |
| Mock LLM | Compare prompt contains hint block when metadata present |
| Postgres integration | Full review path; `metadata.source=playbook_compare` on findings |
| Regression | `PLAYBOOK_ENRICH_COMPARE=false` → behavior matches pre-P4 |

---

## 10. Definition of done (Sprint 4 revised)

1. **Dynamic playbook:** Compare uses **retrieved tenant policy text** + optional **Java metadata hints** — no repo legal rules.  
2. **Traceability:** Findings carry `metadata.source=playbook_compare` and policy ref when available.  
3. **Conflicts explicit:** Unresolved post re-compare → `POLICY_CONFLICT` (not two opposing rows).  
4. **No silent grounding loss:** Failed verify → `INCONCLUSIVE` + coverage backfill if needed.  
5. All new tests pass; existing section-first E2E pass with enrichment flags off.

---

## 11. Explicit non-goals (Sprint 4)

- Regex / YAML rule engine  
- Skip-LLM deterministic paths  
- NLI entailment models (ContractKen-style — Phase 15+)  
- Knowledge graph / Neo4j  
- Word redlines / Ironclad workflow designer  
- Java implementation (document contract only)  

---

## 12. Later phases (not Sprint 4)

| Phase | Enhancement |
|-------|-------------|
| P2 ingest | `sections[]` from Java for stable boundaries |
| P15 | Optional NLI “contradicts playbook” pre-filter before LLM |
| P16 | Bulk review tables (Harvey Vault-style) |

---

## 13. Quick reference — finding `metadata.source`

| Value | Meaning |
|-------|---------|
| `playbook_compare` | Section compare LLM vs retrieved + hints (P4.2) |
| `conflict_resolver` | Unresolved multi-policy conflict (P4.3) |
| `section_first_final` | Final gap verify / re-compare (existing) |
| *(existing)* `gap_type` | no_policy, coverage_backfill, etc. |

Grounding adds `grounding_failed: true` — not a separate source.

---

**Summary:** Sprint 4 = **make the existing LLM compare fully playbook-native** (dynamic metadata + retrieved text) and **harden trust** (conflicts + grounding). Same strategy as Harvey/Ironclad: **customer playbook as data, LLM + citations as judgment.**
