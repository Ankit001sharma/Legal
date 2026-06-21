# Phase 21 P1 — Fix Wrong-Section Quote Bug

**Plan ID:** `DR-PHASE-21-P1-WRONG-SECTION-QUOTE`  
**Priority:** P1  
**Impact:** **Accuracy ★★★★★** — eliminates §8 text on §3 HR cards; **0 extra LLM calls**  
**Depends on:** P0-B unclear re-compare cap (reduces symptom); this plan fixes **root cause**  
**Scope:** `document_core/services/grounding.py`, `grounding_quote.py`, `section_compare_llm.py`, tests  
**Non-goals:** New graph nodes, quote repair rewrite, classifier changes, Java/MCP API redesign

---

## 0. Problem (root cause — verified in code)

**Symptom (Dev UI / Cisco run):** Finding tagged `contract_section_id=§3` (Human Rights) displays **contract quote from §8** (Security/MSS) or another section. Lawyer sees wrong click-to-source.

**Not primarily an LLM “confusion” bug.** Compare-time validation often catches cross-section quotes; **MCP grounding accepts them anyway.**

### Root cause — `document_core/services/grounding.py`

When `GroundingCheckRequest.section_id` is set (e.g. `"3"`), `verify_quote()` builds haystacks in this order:

1. Target section parent text ✓  
2. **Full document canonical text** ✗  
3. **Every parent section in the document** ✗  

```python
# grounding.py L36–68 (simplified)
if request.section_id:
    haystacks.append((parent.text, parent.section_id))  # §3 only
haystacks.append((canonical, request.section_id))       # whole doc
for parent in get_parents(...):
    haystacks.append((parent.text, parent.section_id))  # §1…§11

for text, section_id in haystacks:
    if quote_norm in normalize_text(text):
        return GroundingCheckResult(grounded=True, section_id=section_id)
```

A quote from §8 matches in haystack #2 or #3 → **`grounded=True`** while `grounding_node` keeps `finding.contract_section_id=§3`.

`grounding_node` **never checks** whether the match came from the requested section.

### Secondary gaps (compare path)

| Gap | File | Effect |
|-----|------|--------|
| Quote normalize skipped when `policy_text` empty | `section_compare_llm.py` L225 | Cross-section contract quote may skip pre-grounding check |
| No batch `section_id` filter | `section_compare_llm.py` L220 | LLM can attach wrong `section_id`; item still merged |
| Final-verify items | `final_verify_llm.py` | Same compare batch path; P0-B cap reduces but doesn’t fix verify |

P0-B (unclear re-compare cap) reduced **how often** wrong quotes appear; **did not fix** document-wide grounding.

---

## 1. Design principles (accuracy-safe)

1. **Section-scoped grounding by default** — when `section_id` is provided, quote must appear in **that section’s text only**.
2. **Fail closed** — wrong-section match → ungrounded / INCONCLUSIVE, never silent pass.
3. **Minimal diff** — fix haystack logic + 2 review-agent guards; no new MCP endpoints.
4. **Backward compatible flag** — `strict_section_grounding=true` (default); legacy document-wide search opt-in only if needed.
5. **No LLM** — deterministic substring checks only.
6. **Defense in depth** — fix MCP + compare normalize + grounding_node cross-check.

---

## 2. Fix layers (implement in order)

### P1-Q.1 — Strict section-scoped `verify_quote` (primary)

**File:** `document_core/document_core/services/grounding.py` (~20 lines changed)

**When `request.section_id` is non-empty:**

```python
parent = doc_store.get_parent_by_section(...)
if not parent:
    return GroundingCheckResult(grounded=False, message="section not found", ...)
if quote_norm in normalize_text(parent.text):
    return GroundingCheckResult(grounded=True, section_id=parent.section_id, ...)
return GroundingCheckResult(grounded=False, section_id=request.section_id, ...)
```

**Do not** append canonical or other parents when `section_id` is set.

**When `section_id` is None/empty:** keep current document-wide search (canonical + all parents) for legacy callers.

**Acceptance:**

- [x] Quote in §8, verify with `section_id=3` → `grounded=False`
- [x] Quote in §3, verify with `section_id=3` → `grounded=True`
- [x] Quote in §3, verify with `section_id=None` → `grounded=True` (document-wide still works)

---

### P1-Q.2 — Grounding node cross-check (belt-and-suspenders)

**File:** `review_agent/services/grounding_quote.py` (~12 lines)

After `verify_fn` returns `grounded=True`:

```python
if section_id and check.section_id and check.section_id != section_id:
    return candidate, False, {**meta, "grounding_section_mismatch": True}
```

Apply to contract and policy verify paths.

**Why keep if P1-Q.1 is correct:** protects against future MCP regressions; zero LLM cost.

---

### P1-Q.3 — Compare batch hardening (pre-grounding)

**File:** `review_agent/services/section_compare_llm.py` (~25 lines)

1. **Filter batch items:** drop items where `item.section_id not in section_text_by_id`; warn.
2. **Always validate contract quote** for `COMPLIANT`/`NON_COMPLIANT`:

```python
if item.status in (COMPLIANT, NON_COMPLIANT) and section_text:
    item = _normalize_item_quotes(
        item,
        section_text=section_text,
        policy_text=policy_text or "",  # policy may be empty → contract-only check still runs
    )
```

3. Update `validate_and_normalize_quotes` if needed: when `policy_text` empty but status is NC/C, still require valid `contract_quote` substring (already does for NC/C).

**Acceptance:**

- [x] Batch with §3+§8: item `section_id=3` + §8 quote → INCONCLUSIVE or empty contract_quote before merge
- [x] Unknown `section_id` in LLM output → dropped + warning

---

### P1-Q.4 — Prompt nudge (optional, 3 lines)

**File:** `review_agent/prompts/section_compare.md`

Under quoting rules, add:

> `contract_quote` must come from **the same** `section_id` block you declare. Quotes from other contract sections in this batch are rejected.

No schema change.

---

## 3. Config (optional)

**File:** `review_agent/config.py` (+4 lines)

```python
strict_section_grounding: bool = True  # document_core reads via env or pass-through
```

If implemented in `document_core` only, use env on MCP side:

```env
STRICT_SECTION_GROUNDING=true
```

Prefer **no config** — always strict when `section_id` set (simplest, correct for review pipeline).

---

## 4. Files touched (minimal)

| File | Change | Est. lines |
|------|--------|------------|
| `document_core/services/grounding.py` | Strict haystack when `section_id` set | ~20 |
| `document_core/tests/test_grounding.py` | **New** — cross-section reject | +60 |
| `review_agent/services/grounding_quote.py` | Section mismatch fail-closed | +12 |
| `review_agent/services/section_compare_llm.py` | Batch filter + always normalize contract | +25 |
| `review_agent/prompts/section_compare.md` | 1 prompt line | +3 |
| `review_agent/tests/test_grounding_downgrade.py` | Mismatch + strict mock | +30 |
| `review_agent/tests/test_section_compare.py` | Wrong-section quote downgrade | +40 |

**Total:** ~190 lines. **No new modules required** (optional: `quote_section_guard.py` — avoid unless tests need shared helper).

---

## 5. Code to remove / simplify

| Item | Action |
|------|--------|
| Document-wide haystack loop when `section_id` set | **Remove** from `grounding.py` (bug source) |
| Duplicate substring logic | Keep `quote_is_substring` in review; MCP uses `normalize_text` — no merge needed |
| Workarounds in final-verify only | P0-B cap stays; no removal |

**Do not** remove canonical search entirely — only when `section_id` is provided.

---

## 6. Tests

### 6.1 `document_core/tests/test_grounding.py` (new)

Use in-memory store or mock `DocumentStore`:

| Test | Assert |
|------|--------|
| `test_verify_quote_strict_section_match` | Quote in §3 text, `section_id=3` → grounded |
| `test_verify_quote_rejects_other_section` | Quote only in §8, `section_id=3` → not grounded |
| `test_verify_quote_document_wide_without_section_id` | Quote in §8, `section_id=None` → grounded |

### 6.2 `review_agent/tests/test_section_compare.py`

- Mock LLM returns item with `section_id=s1`, contract_quote from s2 text → after normalize, INCONCLUSIVE or empty quote

### 6.3 `review_agent/tests/test_grounding_downgrade.py`

- Mock `verify_quote` returning `grounded=True, section_id=8` with request section 3 → finding ungrounded / downgraded

### 6.4 Regression

```powershell
cd Legal/document_core
python -m pytest tests/test_grounding.py -q

cd Legal/review/review_agent
python -m pytest tests/test_section_compare.py tests/test_grounding_downgrade.py tests/test_quote_repair_llm.py -q
```

---

## 7. Verification (E2E)

### 7.1 Cisco / Dev UI

Re-run 11-section paste or `run_cisco_assessment.py`.

**Pass criteria:**

| Check | Before | After |
|-------|--------|-------|
| HR finding (§2/§3) contract quote | Sometimes §8 MSS text | **Only §2/§3 text** |
| Click-to-source span | Wrong section highlight | Matches `contract_section_id` |
| `grounding_section_mismatch` in metadata | N/A | 0 in successful NC rows (or row downgraded) |
| Violation count | ~25 | **Same or slightly lower** (wrong rows downgraded, not lost real gaps) |
| LLM calls | baseline | **Unchanged** |

### 7.2 Manual spot check

For each NON_COMPLIANT with quote:

```python
assert finding.contract_quote in section_text_by_id[finding.contract_section_id]
```

(Can add dev-only assertion in artifact builder — optional P1-Q.5.)

---

## 8. LLM call accounting

| Change | LLM impact |
|--------|------------|
| Strict grounding | **0** |
| Compare filter/normalize | **0** |
| Downgrade wrong quotes → INCONCLUSIVE | **−0 to 2** guard calls (fewer bad NC rows) |

**Net:** neutral to slightly fewer downstream calls; **accuracy win is primary**.

---

## 9. Risk matrix

| Risk | Mitigation |
|------|------------|
| Quote spans section boundary in ingest | Rare; document-wide verify when `section_id` omitted; repair uses section fetch |
| Strict verify rejects valid quotes in truncated compare input | Compare already downgrades if quote not in truncated block; repair from full section text in grounding |
| Breaking non-review MCP callers of `verify_quote` | Only strict when `section_id` set; callers without section_id unchanged |
| Policy quote matched in wrong policy section | Same strict logic for `verify_policy_quote` with `policy_section_id` |

---

## 10. Implementation checklist

- [x] **P1-Q.1** Strict `verify_quote` when `section_id` set (`grounding.py`)
- [x] **P1-Q.2** Section mismatch check in `grounding_quote.py`
- [x] **P1-Q.3** Compare batch filter + always contract quote normalize
- [x] **P1-Q.4** Prompt line (optional)
- [x] **P1-Q.5** Unit tests (document_core + review_agent)
- [ ] **P1-Q.6** Cisco / Dev UI re-run — no cross-section quotes on HR rows
- [ ] **P1-Q.7** Optional: `grounding_section_mismatch` counter in artifact ops

---

## 11. Phase sequence

```text
P1 dedupe/cap (done) → **P1 wrong-section quote (this)** → future: artifact quote audit
```

**Orthogonal to** P0-B/P1 guard/dedupe — ship independently; highest accuracy ROI per line changed.

---

## 12. Root-cause diagram

```text
LLM emits section_id=3, contract_quote from §8 block (batch confusion)
        │
        ├─ compare normalize (§3 text) → often INCONCLUSIVE ✓  [P1-Q.3 strengthens]
        │
        └─ if quote slips through ──► grounding_node
                    │
                    └─ verify_quote(section_id=3)
                           searches WHOLE DOCUMENT  ← BUG [P1-Q.1 fixes]
                           grounded=True, card shows §3 title + §8 quote ✗
```

---

*Estimated implementation: 1 focused session (~190 LOC + tests). PR title:*  
*`Youngser P1: strict section-scoped quote grounding (fix wrong-section quotes)`*
