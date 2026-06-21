# Phase 21 P0-B — Cap / Skip Final-Verify Unclear Re-Compare

**Plan ID:** `DR-PHASE-21-P0B-UNCLEAR-CAP`  
**Depends on:** Phase 21 P0-A (`llm_gateway` 429 backoff) — implemented  
**Scope:** `section_merge.py`, `final_verify_llm.py`, `section_compare_llm.py`, `config.py`, tests  
**Goal:** Cut **10–15 redundant LLM calls** per large review **without** dropping true gap recovery or grounded violations  
**Non-goals:** Disable all final-verify, batch guard, classifier redesign

---

## 0. Problem (root cause)

Final-verify **Phase 3** re-runs `compare_section_batch()` **once per section** for every “unclear” finding:

```text
merge → _collect_unclear() marks ALL INCONCLUSIVE + INSUFFICIENT_POLICY_CONTEXT + low confidence
     → 13 unclear findings on 11-section Cisco run
     → up to 11 extra compare LLM calls (Phase 3)
     → wrong quotes (§8 text on §3 findings), duplicate dimensions, 429 spiral
```

| What triggers unclear today | Should re-compare? |
|----------------------------|-------------------|
| Low confidence (`<0.5`) on real playbook compare | **Yes** (1× per section max) |
| Compare failed (JSON/LLM error, non-429) | **No** — P0-A retries at gateway; keep finding |
| Compare failed (429 after retries) | **No** — keep `INSUFFICIENT_POLICY_CONTEXT` |
| INCONCLUSIVE “contract silent / does not mention” | **No** — re-compare cannot invent text |
| Gap `no_policy` / `compare_omitted` | **No** — Phase 1 re-retrieve + Phase 2 gap LLM already handle |
| INCONCLUSIVE with grounded quotes (guard downgrade) | **No** — keep status; guard/grounding owns it |

**Accuracy risk if we blind-disable Phase 3:** lose legitimate low-confidence retries.  
**Fix:** narrow **who** enters Phase 3 + **cap** + **batch** — not delete final-verify.

---

## 1. Design principle (accuracy-safe)

1. **Split “unclear for reporting” vs “unclear for re-compare”** — report all; re-compare only eligible subset.
2. **Max 1 re-compare per contract section** — already deduped by section; enforce cap across sections.
3. **Never supersede grounded NON_COMPLIANT** from main compare with final-verify noise.
4. **Keep Phase 1** (re-retrieve no-policy) and **Phase 2** (gap LLM) unchanged — real accuracy wins.
5. **Keep Phase 4** (conflict re-compare) unchanged in P0-B — rare, high value; cap in P1 if needed.

---

## 2. Eligibility rules (deterministic, no new LLM)

Add `review_agent/services/unclear_recompare.py` (~60 lines) — pure functions, unit-tested:

```python
UnclearReason = Literal[
    "low_confidence",      # eligible
    "compare_failed",      # skip (gateway owns retry)
    "rate_limited",        # skip
    "contract_silent",     # skip
    "gap_context",         # skip — phase 1/2
    "inconclusive_other",  # skip by default
]

def classify_unclear_finding(finding: ComplianceFinding) -> UnclearReason: ...

def eligible_for_unclear_recompare(finding: ComplianceFinding) -> bool:
    """True only for low_confidence playbook_compare with policy context."""
```

### Classification logic (order matters)

```python
meta = finding.metadata or {}
source = meta.get("source", "")
gap_type = meta.get("gap_type", "")
rationale = (finding.rationale or "").lower()

# 1. Gap rows — never Phase 3
if gap_type in ("no_policy", "compare_omitted"):
    return "gap_context"

# 2. Compare failure rows (from _failure_items)
if rationale.startswith("section compare failed:"):
    if "429" in rationale or "rate limit" in rationale or "rate_limited" in rationale:
        return "rate_limited"
    return "compare_failed"  # skip — do not re-compare

# 3. Contract silent INCONCLUSIVE
if finding.status == INCONCLUSIVE and _SILENT_MARKERS in rationale:
    return "contract_silent"

# 4. Eligible: low confidence on primary compare
conf = meta.get("confidence")
if source == "playbook_compare" and conf is not None and float(conf) < 0.5:
    if finding.contract_section_id and (finding.policy_quote or meta.get("policy_document_id")):
        return "low_confidence"  # eligible

return "inconclusive_other"  # skip
```

`_SILENT_MARKERS` tuple (substring match):

```python
("does not mention", "does not reference", "not explicitly", "no explicit",
 "contract silent", "too general", "does not address", "no direct reference")
```

`eligible_for_unclear_recompare` → `classify(...) == "low_confidence"`.

---

## 3. Tag compare failures at source (5 lines)

**File:** `section_compare_llm.py` — `_failure_items()`

Add to each failure item rationale prefix (unchanged) plus when converted to finding:

**File:** `section_merge.py` — `section_items_to_findings()`

```python
if (item.rationale or "").startswith("Section compare failed:"):
    metadata["gap_type"] = "compare_failed"
    metadata["source"] = "section_compare_failed"
```

Ensures classifier sees compare failures without parsing free text elsewhere.

---

## 4. Merge pass — filter queue

**File:** `section_merge.py`

Replace single unclear list usage for final-verify:

```python
unclear_ids = _collect_unclear(merged)  # unchanged — reporting/counts

recompare_ids = [
    fid for f in merged
    if f.finding_id in unclear_ids and eligible_for_unclear_recompare(f)
]

# Tag metadata for audit
for finding in enriched:
    if finding.finding_id in unclear_ids:
        meta["unclear_reason"] = classify_unclear_finding(finding)
        meta["unclear_recompare_eligible"] = finding.finding_id in recompare_ids
```

**Return new field** on `MergeSectionResult`:

```python
unclear_recompare_finding_ids: list[str]  # subset passed to final-verify Phase 3
```

**Wire:** `section_compare_nodes.py` → `final_gap_verify_node` passes `unclear_recompare_finding_ids` instead of full `unclear_finding_ids` to Phase 3 only. Keep full `unclear_finding_ids` on state for report counts.

**State:** `review_state.py` add optional `unclear_recompare_finding_ids: list[str]` (default `[]`).

---

## 5. Final-verify Phase 3 — cap + batch

**File:** `final_verify_llm.py`

### 5.1 Config gates

```python
final_verify_unclear_recompare_enabled: bool = True
final_verify_unclear_recompare_max_sections: int = 4
```

Env: `FINAL_VERIFY_UNCLEAR_RECOMPARE_ENABLED`, `FINAL_VERIFY_UNCLEAR_RECOMPARE_MAX_SECTIONS`.

If `enabled=False` → skip Phase 3 entirely (Phase 1/2/4 still run).

### 5.2 Section selection with cap

After building `unclear_sections` dict (keyed by section_id):

1. **Priority order:** lowest confidence first among eligible findings per section.
2. **Skip section** if main compare already has **grounded NON_COMPLIANT** for same `section_id` (don't overwrite good violations).
3. **`sections_to_recompare = ordered[:max_sections]`**
4. Stats: `unclear_recompare_eligible`, `unclear_recompare_skipped`, `unclear_recompare_capped`.

### 5.3 Batch compare (reuse existing batching)

Replace per-section loop:

```python
# Before: for sid, section in unclear_sections.items(): compare_section_batch([section], ...)
# After:
sections_list = [sections_by_id[sid] for sid in sections_to_recompare]
for batch in chunks(sections_list, cfg.section_compare_batch_size):
    hits_map = {s.section_id: list(bundles[s.section_id].policy_hits) for s in batch}
    items, w = await compare_section_batch(batch, hits_map, ...)
```

**LLM savings:** 4 eligible sections → 2 calls (batch size 2) instead of 4.

### 5.4 Supersede rules (accuracy)

When applying Phase 3 results, supersede **only** findings that were:

- in `unclear_recompare_finding_ids` for that section, **and**
- status ∈ `{INCONCLUSIVE, INSUFFICIENT_POLICY_CONTEXT}` or low confidence

**Do not supersede** `playbook_compare` NON_COMPLIANT with grounded quotes from main pass.

---

## 6. What stays unchanged

| Phase | Behavior |
|-------|----------|
| Phase 1 | Re-retrieve sections with zero policy hits |
| Phase 2 | Gap LLM for still-no-policy sections |
| Phase 4 | Conflict re-compare |
| Main compare | Unchanged |
| Guard / quote repair | Unchanged |
| P0-A gateway | Unchanged |

---

## 7. File checklist (minimal diff)

| File | Change | Lines (est.) |
|------|--------|-------------|
| `services/unclear_recompare.py` | **New** — classify + eligible | +70 |
| `services/section_merge.py` | Filter recompare ids + metadata tags | +25 |
| `services/section_compare_llm.py` | Tag failure item metadata path via merge | 0 (merge only) |
| `services/final_verify_llm.py` | Cap, batch Phase 3, stats, supersede guard | +45 |
| `graph/section_compare_nodes.py` | Pass recompare ids | +5 |
| `state/review_state.py` | Optional field | +2 |
| `config.py` | 2 settings | +4 |
| `tests/test_unclear_recompare.py` | **New** — classify cases | +90 |
| `tests/test_final_gap_verify.py` | Update + add cap/skip tests | +60 |
| `tests/test_section_merge.py` | Eligible subset test | +30 |
| `.env.example` | Document vars | +3 |

**Total production code: ~150 lines across 5 files.**

**Remove:** nothing; no dead code paths until P1 removes unused `needs_final_verify` if redundant (keep for now — audit UI).

---

## 8. Tests (must pass)

### 8.1 `test_unclear_recompare.py`

| Case | eligible |
|------|----------|
| playbook_compare, confidence=0.3, has policy_quote | True |
| rationale `"Section compare failed: 429..."` | False (`rate_limited`) |
| rationale `"Section compare failed: JSON..."` | False (`compare_failed`) |
| INCONCLUSIVE, "contract does not mention MSS" | False (`contract_silent`) |
| gap_type=`no_policy` | False |
| INCONCLUSIVE, confidence=0.8 | False |

### 8.2 `test_final_gap_verify.py`

| Test | Assert |
|------|--------|
| `test_unclear_low_confidence_still_recompared` | Adapt existing `test_unclear_triggers_recompare` — confidence 0.3 |
| `test_unclear_silent_skipped` | 10 silent INCONCLUSIVE → `unclear_recompared=0` |
| `test_unclear_cap_at_four` | 8 eligible sections, max=4 → 2 batched LLM calls, stat capped=4 |
| `test_non_compliant_not_superseded` | Section with grounded NON_COMPLIANT + low-conf INCONCLUSIVE → NON_COMPLIANT kept |

---

## 9. Rollout verification

### Automated

```powershell
cd Legal\review\review_agent
python -m pytest tests/test_unclear_recompare.py tests/test_final_gap_verify.py tests/test_section_merge.py -v
python beta_test/run_cisco_assessment.py   # expect still 6/6
```

### Dev UI (11-section Cisco paste)

**Before P0-B:** ~13 Phase-3 compares, 36 findings, wrong §8 quotes on HR rows  
**After P0-B pass criteria:**

| Metric | Target |
|--------|--------|
| `unclear_recompared` in artifact ops | **0–4** (not 10+) |
| `Rate limit exceeded` in rationales | **0** (with P0-A) |
| §3 recruitment NON_COMPLIANT | **Still present** |
| Main compare violations count | **≥ before** (no loss) |
| Total LLM calls | **−10 to −15** vs pre-P0-B |

---

## 10. Risk matrix

| Risk | Mitigation |
|------|------------|
| Skip legitimate unclear | Only skip non-`low_confidence`; cap=4 tunable via env |
| Lose gap recovery | Phase 1/2 untouched |
| Over-supersede good findings | Supersede guard in §5.4 |
| Under-recompare | Raise `FINAL_VERIFY_UNCLEAR_RECOMPARE_MAX_SECTIONS` |

---

## 11. Done definition

- [x] `eligible_for_unclear_recompare()` unit-tested
- [x] Phase 3 uses filtered ids + cap + batch
- [x] Phase 1/2/4 unchanged
- [x] Grounded NON_COMPLIANT not superseded
- [x] Artifact stats: `unclear_recompare_skipped`, `unclear_recompare_capped`
- [ ] Cisco assessment ≥ 6/6
- [ ] Dev UI large paste: `unclear_recompared` ≤ 4

---

## 12. PR title

`Youngser P0-B: cap and skip final-verify unclear re-compare`

---

*Phase 21 sequence: P0-A rate limit (done) → **P0-B this plan** → P1 batched guard → P1 lexical ESG categories.*
