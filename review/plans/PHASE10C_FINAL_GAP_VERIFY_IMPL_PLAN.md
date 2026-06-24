# Phase 10C — Final Gap Verify Completion (A1 + A2)

**Plan ID:** `DR-PHASE-10C`  
**Scope:** Review agent only — no graph changes, no new MCP tools, no drafting  
**Goal:** Close the last accuracy holes in `final_gap_verify`: LLM confirmation when re-retrieve finds no policy, plus handling of unclear findings and status conflicts from merge.  
**Depends on:** Phase 10 production pipeline (implemented)  
**Estimate:** ~350 lines new/modify, ~150 lines tests, 1 sprint day

---

## 0. Problem statement (verified in code)

| Gap | Current behavior | Risk |
|-----|------------------|------|
| **A1** | `run_final_gap_verify` re-retrieves gaps; if `policy_hits` still empty → `continue` (no output) | Merge’s placeholder `INSUFFICIENT_POLICY_CONTEXT` / `gap_type=no_policy` stays without LLM review of contract-only risk |
| **A2** | `merge_section_findings` sets `unclear_finding_ids` + `conflict_pairs`; `final_gap_verify_node` passes **only** `gap_section_ids` | Unclear INCONCLUSIVE and cross-policy conflicts never get a second pass |
| **Prompt** | `prompts/final_gap_verify.md` exists (58 lines) | **Never loaded** |

**Files today:**

- `review_agent/services/final_verify_llm.py` — re-retrieve + compare only (~105 lines)
- `review_agent/graph/section_compare_nodes.py` — `final_gap_verify_node` L99–145
- `review_agent/services/section_merge.py` — emits gap / unclear / conflict metadata
- `review_agent/tests/test_final_gap_verify.py` — 2 tests (skip disabled, re-retrieve success)

---

## 1. Design principle (minimal, production-grade)

**One service, three work queues, two LLM modes:**

```text
merge_section_findings
  → gap_section_ids | unclear_finding_ids | conflict_pairs
       ↓
final_gap_verify_node (wire all three)
       ↓
run_final_gap_verify (extended)
  Phase 1  Re-retrieve + compare     (existing — gap sections, 0 hits)
  Phase 2  Gap LLM (final_gap_verify.md)  (NEW — still 0 hits after Phase 1)
  Phase 3  Re-compare                (NEW — unclear sections with policy hits)
  Phase 4  Re-compare + conflict ctx (NEW — conflict sections with policy hits)
       ↓
Replace superseded findings → grounding (unchanged)
```

**Do NOT:**

- Add a new graph node or change edge order
- Duplicate compare logic — reuse `compare_section_batch`
- Add a third prompt for conflicts — append context to compare user block
- Call gap LLM when policy hits exist (compare handles that)

**Do:**

- Reuse `get_review_model`, `invoke_structured`, `truncate_section`, `validate_and_normalize_quotes`
- Batch gap LLM calls (batch size 2, same as compare)
- Return explicit `superseded_finding_ids` so the node replaces findings deterministically

---

## 2. Target behavior (acceptance)

### A1 — Pure gap (no policy after re-retrieve)

1. Section in `gap_section_ids`, bundle has 0 hits after `multi_retrieve_for_section` with `final_gap_recall_top_k=30`.
2. **Gap LLM** runs with **contract text only** (per `final_gap_verify.md`).
3. Output finding replaces merge placeholder for that `contract_section_id`:
   - Boilerplate → `INSUFFICIENT_POLICY_CONTEXT` + `info` (confirmed)
   - Visible risk → `NON_COMPLIANT` or `INCONCLUSIVE` + quote from contract
4. `metadata`: `{ "compliance_mode": "section_first_final", "gap_type": "no_policy", "final_verify": "gap_llm" }`
5. `grounding_node` validates `contract_quote` (policy_quote empty OK).

### A2 — Unclear findings

**Unclear** = merge tagged `needs_final_verify` (INCONCLUSIVE / INSUFFICIENT with policy compare, or confidence &lt; 0.5).

1. Resolve `contract_section_id` from each `unclear_finding_id` in `existing_findings`.
2. Skip if section is in **Phase 2 gap LLM** set (no policy — gap LLM owns it).
3. If bundle has policy hits → **re-compare** that section (`compare_section_batch`, batch 1).
4. Optional light re-retrieve first if bundle empty but section was compared (shouldn’t happen) — same as Phase 1.
5. New finding(s) **supersede** the unclear finding_id(s) for that section.

### A2 — Conflicts

**Conflict** = merge detected same `dimension_label`, different `status` (pair in `conflict_pairs`).

1. Collect unique `contract_section_id` from both findings in each pair.
2. Re-compare section with **all** policy hits in bundle + appended user context:

```text
Prior conflicting assessments (resolve to one status per policy dimension):
- [NON_COMPLIANT] Policy doc X §5: "..." — rationale ...
- [COMPLIANT] Policy doc Y §3: "..." — rationale ...
```

3. New finding(s) supersede **both** conflict finding_ids for that section/dimension.

### Node replacement rules

After `run_final_gap_verify` returns:

| Remove | When |
|--------|------|
| Placeholder gap finding | `contract_section_id` resolved in Phase 1–2 with new finding |
| Finding IDs in `superseded_finding_ids` | Unclear/conflict re-compare or gap LLM replacement |
| Stale `INSUFFICIENT` + `gap_type=no_policy` | Same as today if section got policy compare in Phase 1 |

Append all `new_findings` to kept list.

---

## 3. Schema changes (minimal)

**File:** `review_agent/schemas/section_compare.py`

Add:

```python
class FinalGapVerifyItem(BaseModel):
    section_id: str
    status: ComplianceStatus
    severity: Severity = Severity.INFO
    contract_quote: str = ""
    rationale: str = Field(..., min_length=5)

class BatchFinalGapVerifyLLMResult(BaseModel):
    items: list[FinalGapVerifyItem] = Field(default_factory=list)
```

No new file. `FinalGapVerifyItem` is intentionally **policy-free** (matches prompt JSON).

**File:** `review_agent/services/section_merge.py`

Add one small helper (optional, keeps final_verify clean):

```python
def findings_by_id(findings: list[ComplianceFinding]) -> dict[str, ComplianceFinding]: ...
```

Or inline in `final_verify_llm.py` — prefer inline if &lt;10 lines.

---

## 4. Service implementation — `final_verify_llm.py`

### 4.1 New function signature

```python
async def run_final_gap_verify(
    *,
    client: DocumentMCPClient,
    tenant_id: str,
    sections_by_id: dict[str, IndexedChunk],
    bundles: dict[str, SectionRetrievalBundle],
    gap_section_ids: list[str],
    unclear_finding_ids: list[str],
    conflict_pairs: list[tuple[str, str]],
    existing_findings: list[ComplianceFinding],
    contract_type: str | None,
    policy_type: str | None,
    memory_context: str = "",
    settings: ReviewSettings | None = None,
) -> tuple[list[ComplianceFinding], list[str], dict[str, Any], list[str]]:
    # returns: new_findings, warnings, stats, superseded_finding_ids
```

**Early exit:** skip only if `not final_gap_verify_enabled` OR all three input lists empty.

### 4.2 Phase 1 — Re-retrieve gaps (keep existing loop)

Unchanged logic with one fix:

- Today: `if bundle and bundle.policy_hits: continue` — **keep** (don’t re-retrieve sections that already have hits from main pass).

After compare success, track `resolved_gap_section_ids`.

### 4.3 Phase 2 — Gap LLM (`verify_gap_sections_llm`)

**New inner async function** in same file (~80 lines):

```python
async def verify_gap_sections_llm(
    sections: list[IndexedChunk],
    *,
    contract_type: str | None,
    settings: ReviewSettings,
) -> tuple[list[ComplianceFinding], list[str]]:
```

Steps:

1. Load prompt: copy `_load_prompt_template()` pattern from `section_compare_llm.py` → read `prompts/final_gap_verify.md`, split `## SYSTEM` / `## USER`.
2. Build `gaps_block` per section:

```markdown
### Section {section_id} — {title}
Prior status: NO_POLICY (no playbook retrieved after expanded search)
Categories tried: {from bundle or "general"}
```
```
{truncate_section(section.text, section_compare_max_section_chars)}
```

3. Batch sections with `split_batch_by_token_budget` OR simple chunks of `settings.section_compare_batch_size` (reuse config — no new env for v1).
4. `invoke_structured(model, BatchFinalGapVerifyLLMResult, ...)`.
5. For each item:
   - Run `validate_and_normalize_quotes` with `policy_text=""` (contract-only path in `quote_validate.py` already handles non-COMPLIANT/NON_COMPLIANT).
   - Map to `ComplianceFinding` via new `_gap_item_to_finding(item, section)`.
6. On LLM failure → keep merge placeholder (don’t drop gap); add warning + `stats["gap_llm_failed"] += 1`.

**Which sections:**  
`still_gap_ids = [sid for sid in gap_section_ids if not bundles[sid].policy_hits]` after Phase 1.

### 4.4 Phase 3 — Unclear re-compare

```python
findings_map = {f.finding_id: f for f in existing_findings}
unclear_sections: list[IndexedChunk] = []
supersede_ids: list[str] = []

for fid in unclear_finding_ids:
    f = findings_map.get(fid)
    if not f or not f.contract_section_id:
        continue
    sid = f.contract_section_id
    if sid in still_gap_ids:  # gap LLM handled
        continue
    bundle = bundles.get(sid)
    if not bundle or not bundle.policy_hits:
        continue  # belongs in gap path
    supersede_ids.append(fid)
    # dedupe sections
```

Group by section_id → one `compare_section_batch` per section (or batch 2 sections if small).

Add findings via `section_items_to_findings(..., pipeline="section_first_final")`.

### 4.5 Phase 4 — Conflict re-compare

```python
def _conflict_sections_and_context(
    pairs: list[tuple[str, str]],
    findings_map: dict[str, ComplianceFinding],
) -> dict[str, str]:  # section_id -> prior_context block
```

For each pair, append formatted prior finding to section’s context string.

Call `compare_section_batch` with new optional kwarg:

```python
extra_user_context: str = ""
```

Implemented in `section_compare_llm.py` as append after `sections_block` in user template (3-line change).

Supersede **both** finding IDs in each pair when new results arrive for that section.

If re-compare still yields conflicting statuses for same dimension → set one finding `POLICY_CONFLICT` (LLM should prefer this; else merge duplicate statuses in post-pass — **v1:** trust LLM + warning).

### 4.6 Stats object (ops / report)

```python
stats = {
    "gap_sections": len(gap_section_ids),
    "unclear_findings": len(unclear_finding_ids),
    "conflict_pairs": len(conflict_pairs),
    "re_retrieved": 0,
    "resolved_with_policy": 0,
    "gap_llm_sections": 0,
    "gap_llm_failed": 0,
    "unclear_recompared": 0,
    "conflicts_recompared": 0,
    "superseded_count": 0,
    "new_findings": 0,
}
```

---

## 5. Node change — `section_compare_nodes.py`

**Only `final_gap_verify_node`** (~15 lines changed):

```python
new_findings, warnings, stats, superseded_ids = await run_final_gap_verify(
    ...
    unclear_finding_ids=list(state.get("unclear_finding_ids") or []),
    conflict_pairs=[tuple(p) for p in (state.get("conflict_pairs") or [])],
    existing_findings=existing,
)

superseded_set = set(superseded_ids)
resolved_gap_ids = {f.contract_section_id for f in new_findings if f.contract_section_id}

kept_findings = [
    f for f in existing
    if f.finding_id not in superseded_set
    and not (
        f.contract_section_id in resolved_gap_ids
        and f.status == ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT
        and f.metadata.get("gap_type") == "no_policy"
    )
]
```

No changes to `review_graph.py`, `review_state.py` (fields already exist).

---

## 6. Config & env

**Reuse existing** (no new required env):

| Var | Use |
|-----|-----|
| `FINAL_GAP_VERIFY_ENABLED` | Master switch (already true) |
| `FINAL_GAP_RECALL_TOP_K` | Phase 1 re-retrieve (30) |
| `SECTION_COMPARE_BATCH_SIZE` | Gap LLM batch size |
| `SECTION_COMPARE_MAX_SECTION_CHARS` | Truncation |
| `COMPLIANCE_LLM_*` | Same model as compare |

**Optional v1.1** (not required for this sprint):

```env
FINAL_GAP_LLM_ENABLED=true   # default true when FINAL_GAP_VERIFY_ENABLED
```

---

## 7. Prompt — `final_gap_verify.md`

**No rewrite required.** Wire as-is.

Minor optional tweak (1 line in USER section):

```markdown
Contract type: {contract_type}

{memory_context}

Gap sections and prior unclear findings:
{gaps_block}
```

Pass empty `memory_context` if none — keeps parity with compare.

---

## 8. `section_compare_llm.py` — tiny extension

Add optional parameter to `compare_section_batch`:

```python
extra_user_context: str = "",
```

In user message construction:

```python
user = user_tpl.format(contract_type=..., sections_block=sections_block + memory_block)
if extra_user_context.strip():
    user += "\n\n" + extra_user_context.strip()
```

**~5 lines.** Enables conflict re-compare without a new prompt file.

---

## 9. Tests — `test_final_gap_verify.py`

| Test | Mocks | Assert |
|------|-------|--------|
| `test_gap_llm_runs_when_no_hits_after_retrieve` | `multi_retrieve` → empty hits; mock `invoke_structured` → NON_COMPLIANT | `stats["gap_llm_sections"]==1`, finding has `final_verify=gap_llm` |
| `test_gap_llm_skipped_when_phase1_resolves` | retrieve returns hits + compare returns item | gap LLM not called |
| `test_unclear_triggers_recompare` | unclear finding with policy hits; mock compare | superseded unclear id, new finding present |
| `test_conflict_triggers_recompare_with_context` | conflict pair same section; mock compare; assert extra_user_context contains both statuses | both ids superseded |
| `test_node_supersedes_placeholder_and_unclear` | integration-style via node optional | kept findings count correct |
| `test_disabled_skips_all_phases` | existing test — extend to pass unclear/conflict args |

**No Postgres required** — all unit tests with monkeypatch.

Run:

```powershell
cd Legal\review\review_agent
python -m pytest tests/test_final_gap_verify.py -q
```

---

## 10. Sprint tasks (ordered)

| ID | Task | File(s) | Lines |
|----|------|---------|-------|
| 10C.1 | Add `FinalGapVerifyItem` + batch schema | `schemas/section_compare.py` | +20 |
| 10C.2 | `verify_gap_sections_llm` + prompt loader + `_gap_item_to_finding` | `final_verify_llm.py` | +90 |
| 10C.3 | Extend `run_final_gap_verify` Phases 1–4 + stats + supersede list | `final_verify_llm.py` | +80 mod |
| 10C.4 | `extra_user_context` on compare batch | `section_compare_llm.py` | +5 |
| 10C.5 | Wire unclear + conflict into node | `section_compare_nodes.py` | +15 |
| 10C.6 | Tests (6 cases) | `test_final_gap_verify.py` | +150 |
| 10C.7 | Update `.env.example` comment only | optional | +2 |

**Total:** ~360 lines. **Zero deletions** except simplifying early-exit condition.

---

## 11. Edge cases (explicit)

| Case | Handling |
|------|----------|
| Section in both gap + unclear | Gap path wins; skip unclear re-compare for that sid |
| Gap LLM returns wrong `section_id` | Drop item + warning (don’t attach to wrong section) |
| Gap LLM empty `items` | Keep merge placeholder + warning |
| Unclear finding is gap placeholder | Already in `gap_section_ids` → Phase 2 only |
| Conflict across different sections | Re-compare each section independently |
| Compare fails in Phase 3/4 | Keep original finding + warning (no data loss) |
| `FINAL_GAP_VERIFY_ENABLED=false` | Return empty; merge findings unchanged |
| Very long contract section | Same truncation as compare; warning in stats |

---

## 12. Acceptance checklist (Phase 10C DONE)

- [ ] `final_gap_verify.md` loaded and used in production path  
- [ ] Every `gap_section_id` with 0 hits after re-retrieve gets LLM finding or explicit failure warning  
- [ ] Every `unclear_finding_id` with policy hits gets re-compare or remains with warning  
- [ ] Every `conflict_pair` triggers re-compare with prior context; both IDs superseded on success  
- [ ] `final_verify_stats` reports phase counts for ops/report  
- [ ] `pytest tests/test_final_gap_verify.py -q` green  
- [ ] No new graph nodes; grounding/report unchanged  

---

## 13. Out of scope (defer)

- Phase 8 contract-by-ID / Java sections (improves gaps upstream, not this plan)
- Separate LLM prompt for conflicts
- Embedding-based conflict detection
- Caching gap LLM results across threads

---

**Critical path:** 10C.2 (gap LLM) → 10C.3 (orchestration) → 10C.5 (node) → 10C.6 (tests).

*Safety net: no silent gaps; unclear and conflicts get a second pass; minimal diff in one service + one node.*
