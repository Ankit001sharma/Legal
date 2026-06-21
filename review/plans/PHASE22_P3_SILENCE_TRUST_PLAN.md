# Phase 22 P3 — Silence vs “Reviewed With Gap” (Lawyer-Facing Status)

**Plan ID:** `DR-PHASE-22-P3-SILENCE-TRUST`  
**Priority:** P3 (trust blocker — report rows with no substantive verdict)  
**Impact:** **+0–2 LLM calls/contract** (compare_omitted recovery only); **+25–40% actionable findings** on remaining silent sections  
**Depends on:** Phase 22 P1 (discovery scope), P2 (classifier/general), Phase 10C final gap verify, Phase 21 P0-B unclear cap  
**Scope:** `final_verify_llm.py`, `section_merge.py`, `section_coverage.py`, `section_gap_status.py` (new), prompts, config, tests  
**Non-goals:** New graph nodes, new `ComplianceStatus` enum, discovery/classifier rewrites, UI redesign, Phase 14 audit artifact (orthogonal)

---

## 0. Verified root cause (code + benchmark)

### Symptom → lawyer experience

```text
Section appears in report (has contract_section_id row)
  → status = INSUFFICIENT_POLICY_CONTEXT, severity = info
  → no contract_quote / policy_quote / negotiation hook
  → lawyer reads as “handled” but gets no verdict
  → looks like silence dressed as coverage
```

**Production impact:** Report satisfies Phase 10D “every section has a row” but **fails the legal ops bar** — counsel cannot distinguish:

| Intended meaning | What they need | What they get today |
|------------------|----------------|---------------------|
| **Reviewed with gap** | `INCONCLUSIVE` or `NON_COMPLIANT` + contract quote + “playbook X missing / clause silent” | `INSUFFICIENT_POLICY_CONTEXT` |
| **True non-reviewable** | `INSUFFICIENT` for boilerplate (definitions, notices) | Same status — overloaded |
| **Pipeline failure** | `INSUFFICIENT` + `gap_type=compare_failed` | Same status — overloaded |

**Enterprise 40+ context:** P1/P2 shrink `no_policy` by widening discovery and fixing retrieval. **Remaining silence is mostly semantic + one final-verify bug**, not missing policies.

### Evidence (scale 12×43, post-P1 partial)

| Metric | Value | Implication |
|--------|-------|-------------|
| `sections_insufficient` | 3–6 / 20 per contract | Rows exist but non-actionable |
| `retrieval_zero_hit_sections` | **0** (post-P2) | Retrieval fixed; silence ≠ empty RAG |
| `unclear_recompared` | **0** | Phase 3 skipped by design (P0-B) |
| `gap_llm_sections` | low vs gap count | Gap LLM confirms `INSUFFICIENT` too often |
| `backfill_count` | >0 on some runs | Cosmetic row, same status |

### Root causes (precise)

| # | Root cause | File / mechanism | Effect |
|---|------------|------------------|--------|
| **RC-1** | **`INSUFFICIENT` overloaded** | `section_merge.py` L122–132, `section_coverage.py` L68, `section_compare_llm.py` L117 | One status for boilerplate, playbook gap, compare failure, and pipeline drop |
| **RC-2** | **`compare_omitted` never recovered in final verify** | `final_verify_llm.py` L292–299: Phase 1 `if bundle.policy_hits: continue` on **all** `gap_section_ids` | Section had policy hits but compare omitted → **stuck forever** as `INSUFFICIENT` |
| **RC-3** | **Gap LLM only runs when `policy_hits=[]`** | `final_verify_llm.py` L341–347 `still_gap_ids` filter | `compare_omitted` with hits skips Phase 2 as well |
| **RC-4** | **Merge gap rows always `INSUFFICIENT`** | `findings_for_no_policy_sections()` — no section substance check | Substantive liability/IP sections get “no policy” not “reviewed gap” |
| **RC-5** | **Coverage backfill is slot-fill only** | `ensure_section_coverage()` L63–81 | Adds row; **does not run compare or upgrade status** |
| **RC-6** | **Unclear re-compare intentionally skips gap rows** | `unclear_recompare.py` L38–39 `gap_context`; P0-B design | `unclear_recompared=0` is **expected** for `no_policy`/`compare_omitted` — not the fix lever |
| **RC-7** | **Gap LLM prompt allows easy `INSUFFICIENT` exit** | `final_gap_verify.md` L22, L36 | Risk-bearing sections without playbook often confirmed as `INSUFFICIENT` instead of `INCONCLUSIVE` + quote |
| **RC-8** | **Compare skips zero-hit sections by design** | `section_compare_nodes.py` L52–56 | Correct; downstream must produce **reviewed gap**, not silence |

### What is NOT the root cause (avoid wrong fixes)

| Misdiagnosis | Why wrong |
|--------------|-----------|
| Re-enable full unclear re-compare (pre-P0-B) | Recreates 429 spiral; gap rows aren’t low-confidence playbook compares |
| Remove `ensure_section_coverage` | Breaks Phase 10D contract; lawyers lose section rows entirely |
| New status enum `REVIEWED_WITH_GAP` | Java/API/clients consume `ComplianceStatus`; use `INCONCLUSIVE` + metadata |
| More discovery policies alone | P1 done; remaining rows often have hits (`compare_omitted`) or need contract-only gap verdict |

---

## 1. Design principles (minimal production patch)

1. **Split meaning, not schema** — keep `ComplianceStatus`; add `metadata.review_outcome` for ops/UI.
2. **Substantive → `INCONCLUSIVE` (or compare verdict)** — playbook missing or clause silent but **contract text reviewed**.
3. **Boilerplate → `INSUFFICIENT`** — definitions, notices, counterparts (lexical `general` title match).
4. **Fix `compare_omitted` recovery** — one targeted final-verify phase; no graph change.
5. **Backfill last** — coverage backfill inherits upgraded status rules; target `backfill_count → 0`.
6. **Do not undo P0-B** — unclear Phase 3 stays narrow; P3 fixes gap paths instead.
7. **Deterministic first, LLM second** — boilerplate/substance via lexical; LLM only where already called (compare, gap LLM).

---

## 2. Target lawyer-facing semantics (after P3)

| `review_outcome` (metadata) | Status | When |
|-----------------------------|--------|------|
| `boilerplate` | `INSUFFICIENT_POLICY_CONTEXT` | Definitions, notices, signatures — no playbook needed |
| `playbook_gap` | `INCONCLUSIVE` | Substantive section; no playbook in tenant scope after re-retrieve |
| `contract_reviewed` | `INCONCLUSIVE` / `NON_COMPLIANT` / `COMPLIANT` | Compare or gap LLM reviewed contract text |
| `pipeline_incomplete` | `INCONCLUSIVE` | `compare_omitted` / `coverage_backfill` after recovery attempt |
| `compare_failed` | `INSUFFICIENT_POLICY_CONTEXT` | LLM/JSON failure (infra — unchanged) |

**Report line examples (rationale templates):**

- Playbook gap: *“No {categories} playbook in discovered scope. Contract section addresses {topic}; marked inconclusive pending playbook alignment.”*
- Compare omitted (recovered): normal compare rationale with quotes.
- Boilerplate: *“Standard {title} provision; no playbook coverage required.”* → `INSUFFICIENT` + `info`.

---

## 3. Target pipeline (unchanged topology)

```text
section_compare (hits only)
        │
        ▼
merge_section_findings
  ├─ gap rows: status = resolve_gap_status(section, gap_type)   [CHANGED RC-4]
  ├─ no_policy_gap_ids[]
  └─ compare_omitted_gap_ids[]                                  [NEW split]
        │
        ▼
final_gap_verify
  Phase 1: re-retrieve no_policy only                           [UNCHANGED]
  Phase 1b: re-compare compare_omitted (hits exist)             [NEW RC-2/3]
  Phase 2: gap LLM for still-empty hits                         [UNCHANGED]
  Phase 2b: upgrade substantive INSUFFICIENT → INCONCLUSIVE     [NEW RC-7]
        │
        ▼
ensure_section_coverage (substantive backfill → INCONCLUSIVE)   [CHANGED RC-5]
        │
        ▼
grounding → report
```

---

## 4. Implementation tasks

### P3-1. Gap status resolver (~45 lines, new file)

**File:** `review_agent/services/section_gap_status.py`

```python
ReviewOutcome = Literal["boilerplate", "playbook_gap", "pipeline_incomplete", "compare_failed", "contract_reviewed"]

def is_boilerplate_section(section: IndexedChunk) -> bool:
    """Title-level lexical general (definitions, notices, counterparts)."""
    lex = infer_lexical_classify(section)
    return lex.confidence == "title" and lex.categories == ["general"]

def resolve_gap_finding_status(
    section: IndexedChunk | None,
    *,
    gap_type: str,
) -> tuple[ComplianceStatus, ReviewOutcome, str]:
    """Return (status, review_outcome, rationale_suffix)."""
```

**Rules (order matters):**

| `gap_type` | Boilerplate? | Status | `review_outcome` |
|------------|--------------|--------|------------------|
| `compare_failed` | — | `INSUFFICIENT` | `compare_failed` |
| `no_policy` | yes | `INSUFFICIENT` | `boilerplate` |
| `no_policy` | no | `INCONCLUSIVE` | `playbook_gap` |
| `compare_omitted` | — | `INCONCLUSIVE` | `pipeline_incomplete` |
| `coverage_backfill` | yes | `INSUFFICIENT` | `boilerplate` |
| `coverage_backfill` | no | `INCONCLUSIVE` | `pipeline_incomplete` |

Reuse `infer_lexical_classify` from `section_category_lexical.py` — **0 new LLM**.

---

### P3-2. Merge — split gap queues + resolved status (~35 lines)

**File:** `section_merge.py`

1. Add optional `sections_by_id: dict[str, IndexedChunk] | None` to `merge_section_findings()`.
2. In `findings_for_no_policy_sections()`, accept `sections_by_id`; call `resolve_gap_finding_status()`.
3. Extend `MergeSectionResult`:

```python
no_policy_gap_ids: list[str] = field(default_factory=list)
compare_omitted_gap_ids: list[str] = field(default_factory=list)
# gap_section_ids = no_policy + compare_omitted (backward compat)
```

4. Wire `merge_section_findings_node` to pass `_load_sections()` as `sections_by_id`.

**Metadata on every gap finding:**

```python
metadata={
    "gap_type": gap_type,
    "review_outcome": outcome,
    "compliance_mode": "section_first",
}
```

---

### P3-3. Final verify Phase 1b — `compare_omitted` re-compare (~40 lines)

**File:** `final_verify_llm.py`

**Change Phase 1 loop** — only skip re-retrieve when hits exist; **do not** `continue` the whole section:

```python
# Before (bug RC-2):
for section_id in gap_section_ids:
    if bundle and bundle.policy_hits:
        continue

# After:
for section_id in no_policy_gap_ids:
    ...  # existing re-retrieve + compare on new hits
```

**Add Phase 1b** after Phase 1:

```python
for section_id in compare_omitted_gap_ids:
    bundle = bundles.get(section_id)
    if not bundle or not bundle.policy_hits:
        continue
    items, w = await compare_section_batch([section], hits_map, ...)
    new_findings.extend(section_items_to_findings(items, pipeline="section_first_final"))
    superseded_ids.extend(_finding_ids_for_section(existing, section_id, gap_types={"compare_omitted", "no_policy"}))
```

**Wire:** `final_gap_verify_node` passes `no_policy_gap_ids` + `compare_omitted_gap_ids` from state (fallback: derive from findings metadata if missing — migration-safe).

**State keys (optional, minimal):**

```python
no_policy_gap_ids: list[str]
compare_omitted_gap_ids: list[str]
```

---

### P3-4. Gap LLM post-upgrade (~25 lines)

**File:** `final_verify_llm.py` — after `verify_gap_sections_llm()` merge

```python
def _upgrade_substantive_gap_findings(
    findings: list[ComplianceFinding],
    sections_by_id: dict[str, IndexedChunk],
) -> list[ComplianceFinding]:
    ...
```

If gap LLM returns `INSUFFICIENT` for non-boilerplate section → upgrade to `INCONCLUSIVE`, set `review_outcome=playbook_gap`, require empty `contract_quote` → keep but add rationale suffix.

**Prompt tweak** (`final_gap_verify.md`, ~8 lines):

- After L37, add: *“For substantive commercial sections (liability, indemnity, data, termination, IP, security) when no playbook matched: prefer `INCONCLUSIVE` with `contract_quote` over `INSUFFICIENT`.”*

---

### P3-5. Coverage backfill upgrade (~20 lines)

**File:** `section_coverage.py`

Add optional `sections_by_id: dict[str, IndexedChunk] | None`.

For each uncovered section:

```python
status, outcome, suffix = resolve_gap_finding_status(section, gap_type="coverage_backfill")
rationale = base_rationale + suffix
metadata["review_outcome"] = outcome
```

**Wire:** `final_gap_verify_node` passes `sections_by_id` into `ensure_section_coverage()`.

---

### P3-6. Supersede filter alignment (~5 lines)

**File:** `section_compare_nodes.py` L150–159

Extend `_gap_types` supersede on resolve to include findings with `review_outcome in ("playbook_gap", "pipeline_incomplete")` when new finding is `playbook_compare` or `final_verify`.

---

### P3-7. Config + `.env.example` (~8 lines)

**File:** `config.py`

```python
gap_status_substantive_inconclusive: bool = True  # P3 master switch
gap_upgrade_after_gap_llm: bool = True
```

No new LLM concurrency settings. Phase 1b uses existing `compare_section_batch` + batch size.

---

### P3-8. Report metadata (ops, ~10 lines)

**File:** `nodes.py` `report_node`

Add to `report.metadata`:

```python
"gap_status_summary": {
    "insufficient_boilerplate": N,
    "inconclusive_playbook_gap": N,
    "compare_omitted_recovered": N,
    "coverage_backfill": N,
}
```

Count from `metadata.review_outcome` on final findings — helps SRE tune P1 discovery without reading every row.

---

## 5. File touch list

| File | Change | Est. lines |
|------|--------|------------|
| `services/section_gap_status.py` | **New** — boilerplate + status resolver | +45 |
| `services/section_merge.py` | Gap status, split IDs, sections_by_id | +35 |
| `services/final_verify_llm.py` | Phase 1 split, Phase 1b, post-upgrade | +65 |
| `services/section_coverage.py` | Substantive backfill status | +20 |
| `graph/section_compare_nodes.py` | Wire IDs + coverage sections | +15 |
| `state/review_state.py` | Optional gap ID lists | +4 |
| `prompts/final_gap_verify.md` | Substantive → INCONCLUSIVE nudge | +8 |
| `config.py` + `.env.example` | 2 flags | +10 |
| `graph/nodes.py` | gap_status_summary | +10 |
| `tests/test_section_gap_status.py` | **New** | +70 |
| `tests/test_section_merge.py` | Substantive vs boilerplate gaps | +40 |
| `tests/test_final_gap_verify.py` | Phase 1b compare_omitted | +55 |
| `tests/test_section_coverage.py` | Backfill INCONCLUSIVE | +25 |

**Total:** ~400 lines (incl. tests). **No graph topology change.**

---

## 6. Tests (must pass)

| Test | Setup | Assert |
|------|-------|--------|
| `test_boilerplate_definitions_insufficient` | Section title “Definitions” | `INSUFFICIENT`, `review_outcome=boilerplate` |
| `test_substantive_liability_inconclusive` | Title “Limitation of Liability”, no hits | `INCONCLUSIVE`, `playbook_gap` |
| `test_compare_omitted_inconclusive_pending_recovery` | Merge with hits, no compare item | `INCONCLUSIVE`, `pipeline_incomplete` |
| `test_final_verify_recompares_compare_omitted` | Mock hits, empty compare → Phase 1b | `playbook_compare` finding; old gap superseded |
| `test_final_verify_no_policy_still_reretrieves` | no hits → Phase 1 re-retrieve | unchanged behavior |
| `test_gap_llm_upgrades_substantive_insufficient` | Mock gap LLM returns INSUFFICIENT for liability | Upgraded to INCONCLUSIVE |
| `test_coverage_backfill_substantive` | Uncovered indemnity section | `INCONCLUSIVE`, not INSUFFICIENT |
| **Regression** | `test_unclear_recompare_skips_gap_context` | P0-B unchanged |
| **Regression** | `test_merge_adds_no_policy_gap` | Still one row per gap section |

---

## 7. Verification (E2E)

| Run | Before P3 | Target after P3 |
|-----|-----------|-----------------|
| Scale avg `sections_insufficient` | 3–6 / 20 | **≤2 / 20** (boilerplate only) |
| Scale rows with `review_outcome=playbook_gap` | 0 | **>0** on niche contracts |
| `compare_omitted` recovery rate | 0% | **≥80%** when hits exist |
| `coverage_backfill_count` | >0 | **→0** on clean runs |
| `unclear_recompared` | 0 | **0** (unchanged — by design) |
| Cisco 6-section | 6/6 | 6/6 (no regression) |

```powershell
cd Legal\review\review_agent
python -m pytest tests/test_section_gap_status.py tests/test_final_gap_verify.py tests/test_section_merge.py tests/test_section_coverage.py -q

cd Legal\temp_java_sync
python beta_test\run_cisco_assessment.py
python beta_test\run_scale_benchmark.py
```

**Acceptance query on artifact:**

```python
substantive = [f for f in findings if f.metadata.get("review_outcome") == "playbook_gap"]
assert all(f.status == INCONCLUSIVE for f in substantive)
assert sum(1 for f in findings if f.status == INSUFFICIENT and f.metadata.get("review_outcome") != "boilerplate") == 0
```

---

## 8. Rollout / risk

| Risk | Mitigation |
|------|------------|
| Over-classify boilerplate as substantive | Title-only lexical `general`; unit tests for Definitions/Notices |
| Phase 1b extra LLM calls | Only `compare_omitted` sections (typically 0–2); batched via existing batch_size |
| `INCONCLUSIVE` inflation alarms | `review_outcome` separates playbook gap from ambiguous compare |
| API consumers expect INSUFFICIENT for gaps | Document: INSUFFICIENT = non-reviewable; INCONCLUSIVE = needs counsel / playbook |

**Feature flag:** `GAP_STATUS_SUBSTANTIVE_INCONCLUSIVE=false` restores legacy all-INSUFFICIENT gap rows (rollback without revert).

---

## 9. Implementation checklist

- [x] **P3-1** `section_gap_status.py` + unit tests
- [x] **P3-2** Merge split + resolved status
- [x] **P3-3** Final verify Phase 1b (`compare_omitted`)
- [x] **P3-4** Gap LLM prompt + post-upgrade
- [x] **P3-5** Coverage backfill upgrade
- [x] **P3-6** Supersede filter
- [x] **P3-7** Config + `.env.example`
- [x] **P3-8** Report `gap_status_summary`
- [x] **P3-9** Unit tests green (176 pass)
- [ ] **P3-10** Cisco + scale E2E

---

## 10. Out of scope (later)

| Issue | Phase |
|-------|-------|
| Dev UI badge text “Playbook gap” vs “Not reviewed” | P3b UI (metadata already sufficient) |
| Compare top-1 policy when scope > 1 | Phase 22 P4 |
| Expand unclear re-compare to medium confidence | Only if P3 insufficient; risks 429 |
| Java `ComplianceStatus` API docs | Platform team |
| Niche contract discovery (prof_services 5% coverage) | P1 tuning + routing, not P3 |

**P3 completes the Phase 22 trilogy:** P1 scope → P2 reachability → **P3 verdict semantics**.

---

*End of Phase 22 P3 plan — silence vs reviewed-with-gap.*
