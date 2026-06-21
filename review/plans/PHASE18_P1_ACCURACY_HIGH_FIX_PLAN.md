# Phase 18 — P1 Accuracy-High Fixes (Routing, Classification, LLM Schema)

**Plan ID:** `DR-PHASE-18-P1`  
**Owner:** Youngser  
**Scope:** `review_agent` only — classifier fallback, enum normalization, config verification  
**Goal:** Restore correct policy discovery and retrieval when LLM classify/routing hiccups; stop Pydantic batch drops from model typos.  
**Depends on:** Phase 17 P0 (`langchain` installed, `SearchRequest.metadata`, MCP single-instance)  
**Estimate:** ~220 lines code + ~180 lines tests + 1 sprint day  
**Status:** P1-4 and P1-5 implemented; P1-3.V verified — Youngser sign-off pending beta E2E

---

## 0. Executive summary

Three P1 defects degrade accuracy **after** the pipeline runs (unlike P0 total failures):

| ID | Defect | Status | Accuracy impact |
|----|--------|--------|-----------------|
| **P1-3** | Missing `review_plan_llm_max_tokens` | ✅ Fixed in P0 | Routing LLM crash → lexical-only discovery |
| **P1-4** | Classifier fallback → `categories=["general"]` | 🔴 Open | Wrong category filter → missed liability/indemnity playbooks |
| **P1-5** | LLM enum typo not normalized on all schemas | 🟡 Partial | Whole compare/gap batch rejected → placeholder findings |

**Youngser solution:** Reuse proven lexical patterns from `contract_routing.py` for classifier fallback; centralize `ComplianceStatus` normalization across all LLM output schemas.

---

## 1. P1 bug register

### P1-3 — Missing `review_plan_llm_max_tokens` (verification only)

#### Finding

```
contract routing LLM attempt 1 failed: 'ReviewSettings' object has no attribute 'review_plan_llm_max_tokens'
```

#### Root cause

`contract_routing.py:244` read `settings.review_plan_llm_max_tokens` before the field existed on `ReviewSettings`.

#### Youngser solution (done)

| Step | Action | File |
|------|--------|------|
| 1 | Add field default `1024` | `review_agent/config.py` L36 |
| 2 | Regression test | `review_agent/tests/test_config.py` |

#### P1-3 verification subtask (Youngser)

| Task | Assert |
|------|--------|
| P1-3.V1 | `route_contract()` with mocked LLM receives `max_tokens=1024` (monkeypatch `get_review_model`) |
| P1-3.V2 | E2E log has no `review_plan_llm_max_tokens` AttributeError |
| P1-3.V3 | `discovered_policies >= 3` on NDA fixture when routing LLM healthy |

**No new code required** unless V1 test missing — add ~25 lines to `tests/test_contract_routing.py`.

---

### P1-4 — Section classifier fallback → `categories: ["general"]`

#### Finding

```
section classify LLM failed for 3: No module named 'langchain'
section classifier fallback for 3: ... (using categories=[general])
```

Or (LLM up but batch parse fail):

```
classifier omitted section in batch response
→ categories=["general"]
```

With `retrieval_category_hard_filter=True`, metadata search uses `general` only. Playbooks tagged `liability`, `indemnification`, `confidentiality` are **excluded**.

#### Root cause (precise)

1. **Fallback design:** `_fallback_result()` in `section_classifier.py:44–55` hardcodes `categories=["general"]` for **any** LLM/parse failure.
2. **No lexical inference:** Unlike `route_contract()` which falls back to `route_contract_lexical()` with `_TOPIC_KEYWORDS`, classifier has no title/body heuristic path.
3. **Batch gap:** `classify_all_sections()` L184–186 logs `classify batch failed` and **skips** sections when `gather_limited` returns `BaseException` — those sections never get a fallback entry.
4. **Category alias gap:** Fixtures/Java sync use `indemnification`; taxonomy + classifier prompt use `indemnity` (`taxonomy.py` L13). `normalize_categories()` does not alias → filter mismatch even when LLM returns `"indemnification"`.

#### Youngser solution (optimal)

Replace blind `general` fallback with **lexical category inference** mirroring contract routing keywords, plus category alias map.

```text
LLM classify success → use LLM categories (unchanged)
LLM classify failure → infer_categories_from_section(title, text) → normalized taxonomy tags
Still empty          → ["general"] + classify_warning (last resort)
```

**Do not** disable `retrieval_category_hard_filter` globally — that increases noise. Fix categories at source.

---

### P1-5 — LLM enum typo not normalized on all schemas

#### Finding

```
gap LLM failed for batch starting 1: 1 validation error for BatchFinalGapVerifyLLMResult
items.1.status
  input_value='INSUFFICIENT_POLIC_CONTEXT'
```

#### Root cause

1. Mistral (and other models) occasionally drop characters in long enum strings.
2. Normalizer exists only on `FinalGapVerifyItem` (`section_compare.py:34–38`).
3. Same typo on `SectionCompareItem`, `ComplianceLLMResult`, `BatchComplianceItem` still fails structured parse → entire batch dropped.

#### Youngser solution (optimal)

Single shared normalizer used by all LLM schemas with `ComplianceStatus` fields:

```python
# review_agent/schemas/compliance_status_utils.py
_STATUS_TYPO_MAP = {
    "INSUFFICIENT_POLIC_CONTEXT": ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
    "INSUFFICIENT_POLICY_CONTEX": ComplianceStatus.INSUFFICIENT_POLICY_CONTEXT,
    ...
}
```

Apply via `@field_validator("status", mode="before")` on each model **or** one mixin base class.

---

## 2. Implementation plan — task breakdown

### Sprint order

```text
P1-5 enum normalizer (low risk, immediate batch save)
    → P1-4.3 category alias map
        → P1-4.1 lexical infer module
            → P1-4.2 wire fallback + batch exception path
                → P1-3.V verification tests
                    → beta_assessment + E2E gates
```

---

## 3. P1-5 — Enum typo normalization (Youngser)

### Task P1-5.1 — Shared normalizer module

**File:** `review_agent/schemas/compliance_status_utils.py` (new, ~45 lines)

```python
def normalize_compliance_status(value: object) -> object:
    """Map LLM typos / near-miss strings to ComplianceStatus before Pydantic enum coercion."""
```

| Input | Output |
|-------|--------|
| `INSUFFICIENT_POLIC_CONTEXT` | `INSUFFICIENT_POLICY_CONTEXT` |
| `INSUFFICIENT_POLICY_CONTEX` | `INSUFFICIENT_POLICY_CONTEXT` |
| `NON_COMPLIANT` / `NONCOMPLIANT` | `NON_COMPLIANT` |
| `ComplianceStatus.*` instance | unchanged |
| Unknown string | pass through (Pydantic raises as today) |

**Acceptance:**

- [ ] Pure function, no LLM imports
- [ ] Documented typo map with comment: extend when new failures seen in logs

---

### Task P1-5.2 — Apply to all LLM output schemas

**Files to modify:**

| Model | File |
|-------|------|
| `SectionCompareItem` | `schemas/section_compare.py` |
| `FinalGapVerifyItem` | `schemas/section_compare.py` (replace inline validator) |
| `ComplianceLLMResult` | `schemas/compliance_llm.py` |
| `BatchComplianceItem` | `schemas/compliance_llm.py` |

Pattern:

```python
@field_validator("status", mode="before")
@classmethod
def normalize_status(cls, value: object) -> object:
    return normalize_compliance_status(value)
```

**Acceptance:**

- [ ] No duplicate typo logic in `FinalGapVerifyItem`
- [ ] All four models use shared helper

---

### Task P1-5.3 — Tests

**File:** `review_agent/tests/test_compliance_status_normalize.py` (new, ~60 lines)

| Test | Input | Expected |
|------|-------|----------|
| `test_typo_insufficient_polic_context` | `INSUFFICIENT_POLIC_CONTEXT` | valid `SectionCompareItem` |
| `test_typo_on_compliance_llm_result` | same on `ComplianceLLMResult` | validates |
| `test_typo_on_final_gap_item` | same on `FinalGapVerifyItem` | validates |
| `test_invalid_status_still_raises` | `NOT_A_STATUS` | `ValidationError` |
| `test_batch_gap_verify_with_one_typo_item` | JSON with mixed valid + typo | batch parses, 2 items |

**Acceptance:**

- [ ] Tests fail if shared helper removed

---

## 4. P1-4 — Lexical classifier fallback (Youngser)

### Task P1-4.1 — Lexical category inference module

**File:** `review_agent/services/section_category_lexical.py` (new, ~80 lines)

Reuse keyword patterns from `contract_routing.py`:

```python
_CATEGORY_KEYWORDS: tuple[tuple[str, str], ...] = (
    (r"liabilit", "liability"),
    (r"indemn", "indemnity"),
    (r"confidential", "confidentiality"),
    (r"terminat", "termination"),
    ...
)

def infer_categories_from_section(section: IndexedChunk) -> list[str]:
    """Derive taxonomy categories from section title + text snippet."""
```

Rules:

1. Scan **title first** (higher weight), then first ~200 chars of body.
2. Return **deduped** list via `normalize_categories()`.
3. If multiple matches (e.g. liability + indemnity in same section), return all.
4. If none match, return `[]` (caller adds `general`).

**Acceptance:**

- [ ] `"Limitation of Liability"` → `["liability"]`
- [ ] `"Indemnification"` → `["indemnity"]` (via alias, P1-4.3)
- [ ] `"Confidential Information"` → `["confidentiality"]`
- [ ] `"Definitions"` → `[]`

---

### Task P1-4.2 — Wire into `_fallback_result()`

**File:** `review_agent/services/section_classifier.py`

**Youngser solution:** change `_fallback_result`:

```python
def _fallback_result(section: IndexedChunk, *, reason: str) -> SectionCategoryResult:
    inferred = infer_categories_from_section(section)
    categories = normalize_categories(inferred) or ["general"]
    logger.warning(
        "section classifier fallback for %s: %s (inferred categories=%s)",
        section.section_id, reason, categories,
    )
    return SectionCategoryResult(
        section_id=section.section_id,
        categories=categories,
        query_terms=[_section_query(section)],
        classify_warning=f"{reason}; lexical_fallback={categories}",
    )
```

**Acceptance:**

- [ ] Liability section + LLM failure → `categories` contains `liability`, not only `general`
- [ ] Warning string includes `lexical_fallback=`

---

### Task P1-4.3 — Category alias map

**File:** `document_core/document_core/schemas/taxonomy.py`

Extend `normalize_categories()`:

```python
_CATEGORY_ALIASES: dict[str, str] = {
    "indemnification": "indemnity",
    "indemnify": "indemnity",
    "data_protection": "privacy",
    "limitation_of_liability": "liability",
    ...
}
```

Apply alias **after** lowercasing, **before** dedupe.

**Files also update (docs only, optional sync):**

- `temp_java_sync/fixtures/policies/indemnification_standard.json` — may keep `indemnification`; alias handles it
- `section_policy_classify.md` — add note that `indemnification` normalizes to `indemnity`

**Acceptance:**

- [ ] `normalize_categories(["indemnification"])` → `["indemnity"]`
- [ ] Existing tests in `document_core` still pass
- [ ] New test `test_category_alias_indemnification`

---

### Task P1-4.4 — Fix `classify_all_sections` exception path

**File:** `review_agent/services/section_classifier.py` L177–188

**Current bug:**

```python
for result in results:
    if isinstance(result, BaseException):
        logger.warning("classify batch failed: %s", result)
        continue  # sections in this batch LOST
```

**Youngser solution:**

Track batch ↔ section mapping; on `BaseException`, call `_fallback_result(section, reason=str(result))` for **each section in that batch**.

Implementation options (pick A):

| Option | Approach |
|--------|----------|
| **A (recommended)** | Wrap `run_batch` to catch and return dict of fallbacks on failure |
| B | Zip batches with results and recover per batch |

**Acceptance:**

- [ ] Simulated batch exception → every section still has classification entry
- [ ] No silent missing keys in `classify_all_sections` output

---

### Task P1-4.5 — Config flag (optional, default on)

**File:** `review_agent/config.py`

```python
section_classify_lexical_fallback: bool = True
```

When `False`, revert to `["general"]` only (debug escape hatch). Default **True**.

**Acceptance:**

- [ ] Env `SECTION_CLASSIFY_LEXICAL_FALLBACK=false` disables inference

---

### Task P1-4.6 — Tests

**File:** `review_agent/tests/test_section_category_lexical.py` (new, ~70 lines)

| Test | Scenario |
|------|----------|
| `test_infer_liability_from_title` | Title match |
| `test_infer_indemnity_from_indemnification_title` | Alias path |
| `test_fallback_uses_lexical_not_general` | Mock LLM raise → categories include `liability` |
| `test_batch_exception_still_classifies_all` | Mock `gather_limited` raise → 2 sections both get fallback |
| `test_empty_section_still_general` | Definitions → `["general"]` |

**Extend:** `tests/test_section_classifier.py` — update `test_classify_failure_returns_general_with_warning` to expect `liability` when title is liability (change fixture title).

---

## 5. P1-3 verification tasks (Youngser)

### Task P1-3.V1 — Routing max_tokens wiring test

**File:** `review_agent/tests/test_contract_routing.py` (+~25 lines)

```python
@pytest.mark.asyncio
async def test_route_contract_passes_review_plan_max_tokens(monkeypatch):
    captured = {}
    def fake_get_review_model(**kwargs):
        captured.update(kwargs)
        ...
    monkeypatch.setattr(..., fake_get_review_model)
    await route_contract(...)
    assert captured.get("max_tokens") == 1024
```

---

## 6. Verification matrix (Youngser sign-off)

After P1 implementation:

```powershell
cd "d:\Ankit_legal\Legal\review\review_agent"
.\scripts\install_deps.ps1
python -m pytest tests/test_compliance_status_normalize.py tests/test_section_category_lexical.py tests/test_section_classifier.py -v

cd "d:\Ankit_legal\Legal\temp_java_sync"
python beta_test\run_assessment.py
python run_full_e2e.py
```

| Gate | Pass criteria | Before P1 | Target after P1 |
|------|---------------|-----------|-----------------|
| **G1** | Classifier fallback on liability section | `categories=["general"]` | `categories` contains `liability` |
| **G2** | Indemnification fixture retrieved | Often missed | Policy hit for section 4 |
| **G3** | Gap LLM with typo status | Batch fails | Batch parses; finding kept |
| **G4** | `discovered_policies` (NDA E2E) | 1–3 | **3** stable |
| **G5** | `retrieval_zero_hit_sections` | 0 (post-P0) | **0** maintained |
| **G6** | `playbook_compare_count` | 3–7 | **≥ 3** with policy quotes on §3/§4 |
| **G7** | Beta legal accuracy | INCONCLUSIVE heavy | §3 **NON_COMPLIANT** preferred |

---

## 7. File touch list

| File | Task | Lines (est.) |
|------|------|--------------|
| `review_agent/schemas/compliance_status_utils.py` | P1-5.1 | +45 new |
| `review_agent/schemas/section_compare.py` | P1-5.2 | +10 / -6 |
| `review_agent/schemas/compliance_llm.py` | P1-5.2 | +12 |
| `review_agent/tests/test_compliance_status_normalize.py` | P1-5.3 | +60 new |
| `review_agent/services/section_category_lexical.py` | P1-4.1 | +80 new |
| `review_agent/services/section_classifier.py` | P1-4.2, P1-4.4 | +35 |
| `document_core/schemas/taxonomy.py` | P1-4.3 | +20 |
| `document_core/tests/test_taxonomy_aliases.py` | P1-4.3 | +25 new |
| `review_agent/config.py` | P1-4.5 | +2 |
| `review_agent/tests/test_section_category_lexical.py` | P1-4.6 | +70 new |
| `review_agent/tests/test_section_classifier.py` | P1-4.6 | ~10 modify |
| `review_agent/tests/test_contract_routing.py` | P1-3.V1 | +25 |

**Total:** ~220 prod + ~180 test

---

## 8. Out of scope (P2 — separate plan `DR-PHASE-19-P2`)

| Item | Why deferred |
|------|--------------|
| P1-6 Rationale guard over-downgrade | Prompt/tuning, not schema/classifier |
| P1-7 Dev UI 0-findings display | Frontend path mismatch |
| `playbook_load_registry` wiring | Feature, not bug |
| Classifier LLM prompt rewrite | After lexical fallback metrics collected |

---

## 9. Definition of done (Youngser)

- [ ] P1-4 and P1-5 code merged with tests green
- [ ] P1-3.V1 test added; no routing AttributeError in E2E logs
- [ ] `beta_assessment`: liability section retrieves playbook; `playbook_compare_count >= 3`
- [ ] Simulated `INSUFFICIENT_POLIC_CONTEXT` no longer drops gap batch
- [ ] Plan checkboxes updated; PR title prefix: `Youngser P1: ...`

---

## 10. Youngser execution checklist

For **each** subtask:

1. **Youngser solution:** cite root cause from §1 in PR description  
2. Add test that **failed before** the fix  
3. Run unit tests for touched module  
4. Re-run `beta_test/run_assessment.py` — attach `retrieval_zero_hit_sections` + `playbook_compare_count` to PR  
5. Do **not** change P0 files unless P1-4.3 alias requires `taxonomy.py` only  

---

## 11. Risk notes

| Risk | Mitigation |
|------|------------|
| Lexical infer over-tags sections | Require keyword in **title** OR strong match in body; cap at 3 categories |
| Alias map too aggressive | Only map known playbook/Java sync variants; unit test each alias |
| Typo map hides real new statuses | Log normalized typos at DEBUG; unknown strings still fail validation |
| Batch classifier still slow | Lexical fallback is sync/fast; no extra LLM calls |

---

*End of Phase 18 P1 plan — Youngser*
