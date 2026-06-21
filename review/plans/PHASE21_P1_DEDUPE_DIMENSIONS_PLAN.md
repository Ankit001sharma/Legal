# Phase 21 P1 — Dedupe Findings + Max Dimensions / Section

**Plan ID:** `DR-PHASE-21-P1-DEDUPE-CAP`  
**Priority:** P1  
**Impact:** **−5 to 10 LLM calls** per large review (guard + repair); faster grounding  
**Accuracy:** ★★★★ (keep all material violations; trim duplicate noise and low-value COMPLIANT rows)  
**Depends on:** P0-A rate limit, P0-B unclear cap, P0-C lexical ESG, P1 guard batch — implemented  
**Scope:** `section_merge.py`, new `finding_dedupe.py`, `section_compare.md`, `config.py`, tests  
**Non-goals:** New compare LLM pass, rule engine, retrieval top_k reduction, Java changes

---

## 0. Problem (root cause)

Compare prompt **explicitly encourages dimension explosion**:

> *"If the policy says 5 things, you should produce up to 5 findings."*  
> *"Analyze **all material compliance dimensions**..."*

With **multiple policy hits per section** (Cisco: up to 5 policies × several sub-checks), merge dedupe is **too weak**:

| Dedupe today | Key | Misses |
|--------------|-----|--------|
| `section_merge.section_items_to_findings` | `(section_id, policy_doc, dimension_label.lower())` | Same label across policies; label variants ("Forced Labor" vs "Recruitment Fees"); same quote, different labels |

### Evidence — Cisco beta (6 sections)

| Metric | Value |
|--------|-------|
| `findings_total` | **48** |
| `violations_with_quotes` | 25 |
| `guard_checked` (pre batch-guard) | 25 |
| Avg findings / section | ~8 |

Many rows are **the same HR violation** repeated against different policy documents with slightly different `dimension_label` text → extra **guard**, **repair**, and report noise — not new legal insight.

**Downstream cost chain per duplicate finding:**

```text
compare (already paid) → grounding MCP → guard LLM → optional repair LLM
```

Dedupe **before grounding** saves guard/repair; cap at compare-merge saves report clutter and conflict pairs.

---

## 1. Design principles (accuracy-safe)

1. **Never drop the only NON_COMPLIANT for a section** — cap fills with COMPLIANT/INFO only after all violations kept.
2. **Never merge findings with materially different `contract_quote`** — different quotes = different violations.
3. **Dedupe is deterministic** — no LLM; pure keys + quote overlap.
4. **Cap is configurable** — default **4 findings / section**; set `0` or high value to disable.
5. **Prompt + code** — soft cap in prompt reduces LLM output; hard cap in merge is safety net.
6. **Single module** — `finding_dedupe.py` (~100 lines); `section_merge` calls it; no graph change.

---

## 2. Dedupe rules (priority order)

Add `review_agent/services/finding_dedupe.py`:

### 2.1 Label normalization

```python
def normalize_dimension_label(label: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
```

### 2.2 Exact dedupe (keep — move to shared helper)

Key: `(section_id, policy_document_id, normalized_label)`  
*Already in `section_items_to_findings`; refactor to use shared helper.*

### 2.3 Quote-anchor dedupe (new — high value)

When **same section** + **same status** + **same normalized contract quote** (or one quote is substring of other):

→ Keep finding with **higher severity**; tie-break **higher confidence**.

Catches: duplicate dimensions on identical quoted text from different policy pairings.

### 2.4 Cross-policy theme dedupe (new — optional, default ON)

When **same section** + **same status** + **same normalized_label** + **contract quotes overlap ≥ 60%** (word tokens):

→ Keep **highest severity**; if tie, keep finding with **longer policy_quote** (more grounded).

**Do NOT** merge across different statuses (COMPLIANT vs NON_COMPLIANT).

Config: `finding_dedupe_across_policies: bool = True`

### 2.5 Skip dedupe for gap rows

Never dedupe `INSUFFICIENT_POLICY_CONTEXT`, `gap_type` in metadata, or `final_verify=gap_llm`.

---

## 3. Max dimensions / section cap

**Config:**

```python
section_compare_max_findings_per_section: int = 4  # 0 = unlimited
```

**Algorithm** (`cap_compare_items_by_section`):

For each `section_id` group after dedupe:

1. Partition: `non_compliant`, `other` (COMPLIANT, INCONCLUSIVE, etc.)
2. Sort each partition: severity (`CRITICAL` > `IMPORTANT` > `INFO`), then `confidence` desc
3. `kept = all CRITICAL NON_COMPLIANT` (no limit on critical violations)
4. Fill remaining budget with `IMPORTANT NON_COMPLIANT`, then `other` until `max` reached
5. Emit warning: `"section {id}: capped {dropped} finding(s) (max {max})"`

**Default max=4:** Cisco §2 HR might keep 4 distinct violations, drop 4 redundant COMPLIANT/duplicate rows.

**Accuracy:** Critical violations never capped; only lower-value rows trimmed.

---

## 4. Prompt soft cap (minimal edit)

**File:** `review_agent/prompts/section_compare.md`

Add under "What you must do":

```markdown
### Output budget (per contract section)

- Return **at most 4 material findings per contract section** unless multiple **distinct** NON_COMPLIANT gaps exist (different contract quotes).
- **Combine** related sub-checks into one finding when they share the same contract quote and status.
- **Prioritize** NON_COMPLIANT and `critical` severity over COMPLIANT observations.
- Do not emit separate findings for the same gap repeated against multiple policy documents — pick the best-matched policy pair.
```

No schema change.

---

## 5. Wiring (minimal diff)

| Step | File | Change |
|------|------|--------|
| P1-D.1 | `finding_dedupe.py` | **New** — normalize, dedupe, cap (~100 lines) |
| P1-D.2 | `section_merge.py` | Call dedupe+cap on `compare_items` before `section_items_to_findings` (~8 lines) |
| P1-D.3 | `config.py` | 2 settings + env |
| P1-D.4 | `section_compare.md` | Soft cap paragraph (~8 lines) |
| P1-D.5 | `section_merge.py` | Refactor inline `seen` set to use dedupe helper (remove duplicate logic) |
| P1-D.6 | Tests | `test_finding_dedupe.py` + extend `test_section_merge.py` |

**No changes to:** `grounding_node`, `guard_pass`, `final_verify_llm`, graph topology.

Optional P1-D.7 (observability only, +5 lines): add `findings_deduped`, `findings_capped` to merge warnings / `compliance_stats`.

---

## 6. Code to remove (minimal cleanup)

| Item | Action |
|------|--------|
| Inline `seen` dedupe in `section_items_to_findings` | **Replace** with call to `dedupe_compare_items` — one code path |
| Duplicate dedupe logic in tests | Use shared fixtures |
| Legacy `compliance_merge.py` | **Do not touch** — already unused in section-first pipeline |

---

## 7. Tests

### 7.1 `tests/test_finding_dedupe.py` (new, ~90 lines)

| Test | Assert |
|------|--------|
| `test_dedupe_same_quote_different_label` | 2 items, same section/quote → 1 kept |
| `test_dedupe_keeps_different_quotes` | Same section, different contract_quote → both kept |
| `test_dedupe_keeps_different_status` | Same quote, COMPLIANT + NON_COMPLIANT → both kept |
| `test_cap_drops_compliant_first` | 6 items, max=4 → all NON_COMPLIANT kept, COMPLIANT trimmed |
| `test_cap_never_drops_critical_nc` | 5 CRITICAL NC → all 5 kept even if max=4 |
| `test_gap_items_not_deduped` | INSUFFICIENT_POLICY_CONTEXT untouched |

### 7.2 `tests/test_section_merge.py`

- Extend `test_merge_dedupes_compare_items` to use cross-policy duplicate scenario
- Add `test_merge_caps_findings_per_section`

### 7.3 Regression

```powershell
cd Legal/review/review_agent
python -m pytest tests/test_finding_dedupe.py tests/test_section_merge.py tests/test_guard_pass.py -q
```

---

## 8. Verification (E2E)

### 8.1 Cisco script

```powershell
cd Legal/temp_java_sync
python beta_test/run_cisco_assessment.py
```

| Metric | Before P1-D | After P1-D |
|--------|-------------|------------|
| `findings_total` | ~48 | **≤ 28** (target ~30–40% reduction) |
| `violations_with_quotes` | 25 | **≥ 20** (no material loss) |
| `legal_accuracy` | 6/6 | **6/6** |
| `guard_checked` | ~10–25 | **−5 to 10** |
| Duplicate HR labels in report | many | **≤ 2 per section** |

### 8.2 Dev UI 11-section paste

- Report cards: no 3+ findings with identical contract quote on same section
- `compliance_stats` shows cap/dedupe warnings if triggered

---

## 9. LLM call accounting

| Stage | Saved |
|-------|-------|
| Compare | **0** (items already produced) |
| Guard (NC only, batched) | **−3 to 8** (fewer NC rows) |
| Guard repair | **−0 to 2** |
| Final-verify unclear | **−0 to 2** (fewer low-conf duplicates) |
| **Total** | **−5 to 10** |

---

## 10. Risk matrix

| Risk | Mitigation |
|------|------------|
| Merge distinct violations with similar labels | Require quote overlap ≥ 60% for cross-policy dedupe |
| Cap hides IMPORTANT NC | Never cap CRITICAL; raise max if warnings show IMPORTANT dropped |
| Over-aggressive prompt cap | Hard cap only drops COMPLIANT after NC budget filled |
| NDA single-section multi-dimension legitimate | max=4 allows 4 distinct quotes; critical uncapped |

---

## 11. Implementation checklist

- [x] **P1-D.1** `finding_dedupe.py` — normalize, dedupe, cap
- [x] **P1-D.2** Wire into `merge_section_findings`
- [x] **P1-D.3** Config + `.env.example`
- [x] **P1-D.4** Prompt soft cap
- [x] **P1-D.5** Remove inline duplicate `seen` logic from `section_items_to_findings`
- [x] **P1-D.6** Unit tests
- [ ] **P1-D.7** Cisco re-run — violations stable, findings_total down
- [ ] **P1-D.8** Optional stats: `findings_deduped`, `findings_capped`

---

## 12. Phase sequence

```text
P0-A (done) → P0-B (done) → P0-C (done) → P1 guard batch (done) → **P1-D dedupe/cap (this)**
```

**Estimated implementation:** ~130 LOC + tests, 1 focused session.

---

*PR title:* `Youngser P1: dedupe compare findings + max 4 dimensions/section`
