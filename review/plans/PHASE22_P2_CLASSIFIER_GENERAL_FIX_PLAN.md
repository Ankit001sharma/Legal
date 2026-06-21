# Phase 22 P2 — Section Classifier: No More `general` Silence

**Plan ID:** `DR-PHASE-22-P2-CLASSIFIER-GENERAL-FIX`  
**Priority:** P2 (accuracy blocker — empty retrieval after P1 discovery scope fix)  
**Impact:** **−2 to −4 classifier LLM calls** (batch retry); **+15–25% section coverage** on security/risk/BCP clauses  
**Depends on:** Phase 21 P2 lexical-first (done), Phase 22 P1 discovery scope (done), Phase 10 taxonomy  
**Scope:** `section_category_lexical.py`, `section_classifier.py`, `multi_retrieval.py`, `config.py`, tests  
**Non-goals:** New graph nodes, LLM classifier removal, compare/guard changes, discovery rewrite

---

## 0. Verified root cause (code + Cisco / scale runs)

### Symptom → mechanism chain

```text
Section title "Risk Management and Business Continuity" (Cisco §6)
  → infer_lexical_classify: no title match (before P2 patterns) OR body-only weak
  → needs_llm → batch classify LLM fails (empty/parse error)
  → _fallback_result → infer_categories_from_section → [] → categories=['general']
  → multi_retrieval attempt 0: category_hard_filter + ['general']
  → list_policy_ids_by_categories('general') → ∅ (no policy tagged general)
  → filter fallback → scope-only BUT query still weak
  → policy_hits=[] OR wrong doc
  → compare skipped → INSUFFICIENT_POLICY_CONTEXT
```

### Evidence

| Run | Section | Title | Classifier output | Retrieval |
|-----|---------|-------|-------------------|-----------|
| Cisco beta | §6 | Risk Management and Business Continuity | `general` | 0 hits → silence |
| Cisco beta | §5 | Supply Chain Security | often `general` when batch fails | HR policy noise |
| Scale benchmark | §6–§7 | Security / BCP titles | `batch section classify failed` → `general` | INSUFFICIENT |

### Root causes (precise)

| # | Root cause | File / behavior | Effect |
|---|------------|-----------------|--------|
| **RC-1** | **Lexical title gaps** | `section_category_lexical.py` — missing `risk management`, `disaster recovery`, `resilience`; BCP/SCV mapped only to `vendor_security` | Obvious titles sent to LLM or fall through to `general` |
| **RC-2** | **Batch LLM failure = whole batch → fallback** | `section_classifier.py` L183–187 — one exception drops all sections in batch to `_fallback_result` | Pairing §5+§6: transient error loses classify for both (if both were in needs_llm) |
| **RC-3** | **Fallback uses `infer_categories_from_section` but empty LLM error** | `_fallback_result(reason=str(exc))` with empty `exc` — hard to debug; no per-section LLM retry | Stays `general` when lexical body scan also misses (200 char cap) |
| **RC-4** | **`general` + hard category filter on attempt 0–1** | `multi_retrieval.py` `_query_for_attempt` attempt 0–1: `use_category_filter=True` with `categories=['general']` | No taxonomy match → empty filter → delayed recovery only on attempt 2 |
| **RC-5** | **Attempt 2 only after 2 failed attempts** | `retrieval_max_attempts=3` but attempt 0 with `general` may return 0 hits without triggering broaden fast enough | Wasted attempts or early exit |
| **RC-6** | **P0-C enrich only on LLM success path** | `_enrich_categories_from_lexical` runs when LLM returns `general`, NOT when batch throws | Batch fail skips enrich even when title clearly says "Responsible Minerals" |

**Production impact:** Wrong or empty category → **wrong policy or no policy** for that section. P1 fixes discovery scope; **P2 fixes per-section category** so retrieval can use scoped policies.

### What P1 partial fix already did

| Change (P1) | Helps | Still gaps |
|-------------|-------|------------|
| `supply chain security`, `business continuity`, `scv` patterns | §5 title, §6 title partial | `Risk Management` alone; batch fail path |
| Discovery category sweep | Policy in scope | Classifier still `general` → retrieval filter weak |

---

## 1. Design principles (minimal patch)

1. **Lexical-first stays** — expand patterns; never replace LLM for Definitions/boilerplate.
2. **Fail open on classify** — batch fail → **per-section single LLM retry** → lexical fallback with **full query terms**.
3. **Never hard-filter on `general`** — attempt 0 uses scope-only search (same as attempt 2 behavior).
4. **One module per concern** — patterns in lexical; retry in classifier; filter rule in retrieval.
5. **0 new graph nodes** — same `classify_all_sections` → `multi_retrieve_for_section` wire.
6. **Cisco-safe** — titled ESG/commercial clauses skip LLM; Definitions still use LLM.

---

## 2. Target flow (after P2)

```text
classify_all_sections
  for each section:
    lexical_first (expanded patterns) → done if title/body confident
  needs_llm → batch LLM (size=2)
    on batch exception:
      per-section single LLM retry (NEW)
      still fail → _fallback_result with infer_lexical_classify + query_terms (IMPROVED)
  never emit general if lexical found any category (NEW guard)

multi_retrieve_for_section
  if categories == ['general']:
    attempt 0: scope-only, NO category_hard_filter (NEW)
  else:
    existing 3-attempt ladder
```

---

## 3. Implementation tasks

### P2-1. Lexical pattern expansion (RC-1) — ~25 lines

**File:** `review_agent/services/section_category_lexical.py`

Add to `_CATEGORY_KEYWORDS` (title-friendly, high precision):

```python
(r"risk management|operational risk|enterprise risk", "vendor_security"),
(r"disaster recovery|\bdr\b plan|resilience", "vendor_security"),
(r"information security|cybersecurity|data security", "security"),
(r"subcontract|sub-contract|flow.?down", "procurement"),
(r"audit rights|right to audit|books and records", "compliance"),
(r"export control|anti.?corruption|\bfcpa\b", "compliance"),
(r"notice|notices", "termination"),  # only if title-only match via title scan
```

Add query terms:

```python
"vendor_security": (
    "vendor security assessment",
    "business continuity SCV",
    "supply chain visibility",
),
"security": (
    "information security controls",
    "master security specification MSS",
),
```

**Rule:** Title scan runs first (`infer_lexical_classify` L107–116). Patterns must match **section titles** in Cisco/scale fixtures.

**Acceptance:**

| Title | Expected categories |
|-------|---------------------|
| `Risk Management and Business Continuity` | `vendor_security` (title) |
| `Supply Chain Security` | `security` (title) |
| `Definitions` | `[]` → LLM required |
| `Responsible Minerals` | `minerals` (title) |

---

### P2-2. Batch-fail → per-section LLM retry (RC-2, RC-3) — ~35 lines

**File:** `review_agent/services/section_classifier.py`

Replace batch exception handler (L183–187):

```python
except Exception as exc:
    logger.warning("batch section classify failed: %s", exc)
    out: dict[str, SectionCategoryResult] = {}
    for section in sections:
        try:
            single = await _classify_batch_llm([section], ...)
            out[section.section_id] = single[section.section_id]
        except Exception as single_exc:
            out[section.section_id] = _fallback_result(
                section, reason=f"batch_and_single_failed:{single_exc}", ...
            )
    return out
```

**Config:**

```python
section_classify_batch_retry_single: bool = True
```

When `False`, keep current all-fallback behavior (tests).

**Acceptance:** Mock batch fail on `[§5,§6]` → single retry called twice; §5 with clear title never ends `general`.

---

### P2-3. Strengthen `_fallback_result` (RC-3, RC-6) — ~15 lines

**File:** `section_classifier.py`

```python
def _fallback_result(...):
    lex = infer_lexical_classify(section)
    if lex.categories:
        categories = normalize_categories(lex.categories)
        terms = infer_query_terms_from_lexical(categories, section)
        warning = f"{reason}; lexical_fallback={categories}"
    else:
        categories = ["general"]
        terms = [_section_query(section)]
        ...
```

Already partially true — ensure **query_terms always policy-oriented** when lexical hits (not `_section_query` contract snippet).

**Optional:** Increase body scan for fallback only:

```python
section_category_lexical._MAX_BODY_SCAN_CHARS  # keep 200 for classify
# OR pass extended scan in fallback via infer_lexical_classify on full title+body[:500]
```

Minimal v1: **title patterns only** (P2-1); defer body scan extension.

---

### P2-4. Never emit `general` when lexical has categories (RC-6) — ~10 lines

**File:** `section_classifier.py` — in `_classify_batch_llm` success path after normalize:

```python
categories = normalize_categories(item.categories) or ["general"]
categories, enrich_note = _enrich_categories_from_lexical(categories, section)
if categories == ["general"]:
    lex = infer_lexical_classify(section)
    if lex.categories:
        categories = normalize_categories(lex.categories)
        enrich_note = f"lexical_override_general={categories}"
```

Also apply in `_fallback_result` guard: if about to emit `general`, re-run `infer_lexical_classify` (covered by P2-3).

---

### P2-5. Retrieval: skip hard filter for `general` (RC-4, RC-5) — ~12 lines

**File:** `multi_retrieval.py` — in `multi_retrieve_for_section` loop:

```python
use_category_filter = wants_category_filter and cfg.retrieval_category_hard_filter
if classification.categories == ["general"] or (
    len(classification.categories) == 1 and classification.categories[0] == "general"
):
    use_category_filter = False
    # scope-only search; title/query_terms still drive dense+FTS
```

**Config (optional override):**

```python
retrieval_skip_hard_filter_for_general: bool = True
```

**Acceptance:** Classified `general` + scoped discovery → still returns hits from scope via title query (Cisco §6 recovery when classify imperfect).

---

### P2-6. Prompt nudge (RC-1 supplement) — ~8 lines

**File:** `prompts/section_policy_classify.md`

Add examples table rows:

| Section title example | categories |
|-----------------------|------------|
| Risk Management and Business Continuity | `vendor_security` |
| Supply Chain Security | `security` |
| Human Rights and Labor | `human_rights`, `labor` |

Reduces LLM returning `general` when lexical misses.

---

### P2-7. Ops / warnings cleanup — ~5 lines

**File:** `section_retrieval_nodes.py`

Rename misleading warning:

```python
# Before: "classifier fallback" for ANY classify_warning
# After: "classifier note" OR only warn when "fallback" in warning / categories==general
```

---

### P2-8. Config + `.env.example`

```env
SECTION_CLASSIFY_BATCH_RETRY_SINGLE=true
RETRIEVAL_SKIP_HARD_FILTER_FOR_GENERAL=true
```

---

## 4. Files touched (minimal diff)

| File | Change | Lines |
|------|--------|-------|
| `section_category_lexical.py` | Patterns + query terms | +30 |
| `section_classifier.py` | Batch retry, fallback, general guard | +55 |
| `multi_retrieval.py` | Skip hard filter for `general` | +12 |
| `config.py` | 2 settings | +6 |
| `section_policy_classify.md` | Example rows | +8 |
| `section_retrieval_nodes.py` | Warning text | +3 |
| `.env.example` | Document settings | +4 |
| `tests/test_section_classifier.py` | 4 new tests | +80 |
| `tests/test_multi_retrieval.py` | 1 test general filter | +25 |

**Not touched:** `discovery_nodes.py`, compare LLM, graph topology.

---

## 5. Tests (must pass)

| Test | Setup | Assert |
|------|-------|--------|
| `test_lexical_risk_management_title` | Title "Risk Management and Business Continuity" | `vendor_security`, skip LLM |
| `test_lexical_supply_chain_security` | Title "Supply Chain Security" | `security`, skip LLM |
| `test_batch_fail_retries_single` | Mock batch fail, single OK for §6 | §6 ≠ `general` |
| `test_batch_fail_all_fallback_lexical` | Mock batch+single fail, minerals title | categories include `minerals` |
| `test_llm_general_overridden_by_lexical` | LLM returns `general`, title minerals | `minerals` |
| `test_retrieval_general_skips_hard_filter` | Mock `general`, scope 2 docs | search uses scope, not empty category filter |
| **Regression** | `test_lexical_first_skips_llm_liability` | unchanged |
| **Regression** | `test_classify_failure_definitions_still_general` | Definitions → LLM or `general` OK |

---

## 6. Verification (E2E)

| Run | Before P2 | Target after P2 |
|-----|-----------|-----------------|
| Cisco §6 | `INSUFFICIENT_POLICY_CONTEXT`, `general` | `INCONCLUSIVE` or `NON_COMPLIANT` with security/risk policy |
| Cisco §5 | Wrong HR policy compare | Top hit security/MSS policy |
| Scale benchmark avg coverage | ~52% | **≥70%** (with P1) |
| Classifier LLM calls (20 sections) | ~6–8 batches | **−2 to −4** calls |

```powershell
cd Legal\temp_java_sync
python beta_test\run_cisco_assessment.py
python beta_test\run_scale_benchmark.py
```

---

## 7. Rollout / risk

| Risk | Mitigation |
|------|------------|
| Over-broad title patterns (e.g. "notice" → termination) | Title-only scan; require title match not body-only for new patterns |
| Extra single LLM retries on batch fail | Only on exception; max 1 retry per section |
| Scope-only retrieval for `general` adds noise | Scoped to discovered policy IDs; reranker trims |
| Definitions classified wrong | Keep `general` + LLM for Definitions; do not add definition patterns |

---

## 8. Implementation checklist

- [x] **P2-1** Lexical patterns + query terms
- [x] **P2-2** Batch-fail per-section retry
- [x] **P2-3** Strengthen `_fallback_result`
- [x] **P2-4** General override from lexical
- [x] **P2-5** Retrieval skip hard filter for `general`
- [x] **P2-6** Prompt examples
- [x] **P2-7** Warning text cleanup
- [x] **P2-8** Config + `.env.example`
- [x] **P2-9** Unit tests
- [x] **P2-10** Cisco E2E re-run (6/6, 10.0/10, §6 fixed)
- [ ] **P2-10b** Scale benchmark re-run (in progress)

---

## 9. Out of scope (later phases)

| Issue | Phase |
|-------|-------|
| `INSUFFICIENT_POLICY_CONTEXT` semantics / lawyer-facing status | P3 silence trust |
| Compare uses wrong policy when scope > 1 | Phase 22 P4 (top-1 / category-aligned compare) |
| `policy_quote` list schema drift | Phase 22 P4 |
| Empty LLM error root cause (Mistral/429) | P0-A monitoring |

**P2 alone** fixes **classifier → retrieval category path** so P1-scoped policies are actually reachable per section.

---

*End of Phase 22 P2 plan — section classifier general → taxonomy fix.*
