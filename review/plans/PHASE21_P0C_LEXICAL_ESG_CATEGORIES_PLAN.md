# Phase 21 P0-C — Expand Lexical Categories (HR, Minerals, ESG)

**Plan ID:** `DR-PHASE-21-P0C-LEXICAL-ESG`  
**Priority:** P0  
**Impact:** **−0 to 6 LLM calls** per supplier/ESG-heavy review (retrieval fix, not a new LLM step)  
**Accuracy:** ★★★★★ (restores policy hits for HR/minerals/environment sections without changing compare/guard)  
**Depends on:** Phase 21 P0-A (rate limit), P0-B (unclear re-compare cap) — both implemented  
**Scope:** `taxonomy.py`, `section_category_lexical.py`, `section_classifier.py`, prompt table, tests  
**Non-goals:** Classifier LLM redesign, routing YAML expansion, ingest/MCP schema changes, new rule engines

---

## 0. Problem (root cause)

Lexical fallback exists (`section_category_lexical.py`) but **does not know supply-chain / ESG vocabulary**. When the section classifier LLM fails (429, batch omission, JSON error) or returns only `general`, ESG-heavy sections get **`categories=["general"]`** → category-filtered retrieval misses indexed policies → `no_policy` / `compare_omitted` → extra gap LLM + final-verify noise.

### Evidence — Cisco beta (6-section supplier contract)

| Section | Title | Indexed policy tags (ingest) | Lexical today | Result |
|---------|-------|------------------------------|---------------|--------|
| §1 | Supplier Code of Conduct | `compliance`, `human_rights` | `[]` → `general` | Code-of-conduct policy not retrieved |
| §2 | Human Rights and Labor | `human_rights`, `labor` | `[]` → `general` | Forced-labor policy not retrieved |
| §3 | Responsible Minerals | `minerals`, `compliance` | `[]` → `general` | MRT/RMAP policy not retrieved |
| §4 | Environment and GHG | `environment`, `sustainability` | `[]` → `general` | GHG/CDP policy not retrieved |

Commercial categories (liability, indemnity, security) **already work** in lexical. Gap is **HR / minerals / ESG only**.

### Why `hr` alone is not enough

- Taxonomy already has `hr` (internal HR handbook: benefits, leave, workplace conduct).
- Cisco / supplier playbooks use **`human_rights`**, **`labor`**, **`minerals`**, **`environment`**, **`sustainability`** — **not** `hr`.
- Mapping everything to `hr` would **still fail** category hard-filter against ingest metadata.

**Accuracy rule:** Lexical output must match **policy ingest `metadata.categories`**, not an abstract umbrella only.

---

## 1. Design principles

1. **Minimal diff** — one keyword table + small taxonomy/alias patch + optional 8-line classifier merge. No new modules.
2. **Title-first, specific-before-general** — reuse existing `_scan_text` / `_MAX_INFERRED_CATEGORIES=3` / `_MAX_BODY_SCAN_CHARS=200`.
3. **No new LLM calls** — pure regex inference; may **avoid** 0–6 downstream compare/gap/guard calls when retrieval succeeds on first pass.
4. **Do not weaken commercial paths** — append new patterns at end of tuple; do not reorder existing liability/indemnity/privacy patterns.
5. **Fix known mis-maps while touching file** — remove `(r"warrant", "insurance")` (warranty ≠ insurance); tighten `(r"assign", "termination")` to assignment context only.
6. **Align LLM prompt taxonomy** — add missing tags to `section_policy_classify.md` so LLM success path also emits correct tags (4 table rows, no prompt rewrite).

---

## 2. Taxonomy alignment (required for retrieval accuracy)

**File:** `document_core/schemas/taxonomy.py` (~25 lines)

### 2.1 Extend `STANDARD_POLICY_CATEGORIES`

Add ingest-facing tags used by Cisco fixtures and supplier playbooks:

```python
"minerals",
"human_rights",
"labor",
"compliance",
"environment",
"sustainability",
```

Keep existing `hr` — used for **internal** HR policies, not supply-chain human rights.

### 2.2 Extend `_CATEGORY_ALIASES` (normalize only, no retrieval expansion)

| Alias (ingest / LLM / Java) | Canonical |
|-----------------------------|-----------|
| `esg` | `environment` |
| `responsible_minerals` | `minerals` |
| `conflict_minerals` | `minerals` |
| `forced_labor` | `human_rights` |
| `modern_slavery` | `human_rights` |
| `ghg` | `environment` |
| `climate` | `environment` |
| `code_of_conduct` | `compliance` |

**Note:** User-facing label is “ESG”; retrieval canonical for GHG sections is **`environment`** (+ `sustainability` when matched). Alias `esg` → `environment` keeps one canonical while allowing LLM/UX to say “ESG”.

### 2.3 Acceptance (taxonomy)

- [ ] `normalize_categories(["esg"])` → `["environment"]`
- [ ] `normalize_categories(["forced_labor", "labor"])` → `["human_rights", "labor"]`
- [ ] `normalize_categories(["minerals"])` → `["minerals"]`
- [ ] Existing liability/indemnity aliases unchanged

---

## 3. Lexical keyword patterns (core change)

**File:** `review_agent/services/section_category_lexical.py` (~30 lines added, ~2 removed)

Append **after** existing commercial patterns (order = specificity):

```python
# Supply-chain HR / human rights (match ingest: human_rights, labor, compliance)
(r"supplier code of conduct|code of conduct|\brba\b|social compliance|saq\b|vap audit", "compliance"),
(r"human rights|forced labor|modern slavery|traffick|bonded labor|indentured labor|freedom of association|un guiding principles|\bilo\b", "human_rights"),
(r"child labor|working hours|wage|recruitment agenc|labor standard", "labor"),

# Internal HR (distinct from supply-chain human_rights)
(r"\bhr\b|human resources|employee benefit|leave polic|workplace conduct", "hr"),

# Responsible minerals (match ingest: minerals)
(r"responsible mineral|conflict mineral|\bmrt\b|\brmap\b|smelter|refiner|\b3tg\b|tin.*tantalum|tungsten.*gold", "minerals"),

# ESG / environment (match ingest: environment, sustainability — not abstract esg alone)
(r"\bghg\b|greenhouse gas|\bcdp\b|carbon emission|emissions reduction|climate target", "environment"),
(r"sustainability|circular design|circular econom|environmental impact", "sustainability"),
```

### 3.1 Pattern safety rules

| Risk | Mitigation |
|------|------------|
| “labor” in payment context | Require **title match** or compound phrases (`child labor`, `recruitment agenc`) — avoid bare `\blabor\b` |
| “environmental laws” boilerplate | `environment` patterns require GHG/CDP/climate/emissions — not bare “environmental” |
| Over-tagging (cap 3) | Title scan stops at 3; minerals + human_rights rarely co-occur with liability |
| Supplier vs internal HR | `code of conduct` → `compliance`; bare `workplace conduct` → `hr` |

### 3.2 Cleanup (same file)

| Remove / fix | Reason |
|--------------|--------|
| `(r"warrant", "insurance")` | False map — warranty clauses are not insurance |
| `(r"assign", "termination")` | Too broad — replace with `(r"assign(ment|able)|transfer of (this )?agreement", "termination")` |

### 3.3 Expected inference (Cisco contract)

| Section title | Expected lexical categories |
|---------------|----------------------------|
| Supplier Code of Conduct | `compliance` |
| Human Rights and Labor | `human_rights`, `labor` |
| Responsible Minerals | `minerals` |
| Environment and GHG Emissions | `environment` (+ `sustainability` if body mentions circular/sustainability) |
| Definitions | `[]` → caller uses `general` |

---

## 4. Classifier merge when LLM returns only `general` (high-value, ~8 lines)

**Problem:** Batch classifier often **succeeds** but returns `general` for topical ESG sections (429 partial JSON, model conservatism). Lexical fallback only runs on **exception**, not on bad LLM output.

**File:** `review_agent/services/section_classifier.py`

Add helper:

```python
def _enrich_categories_from_lexical(
    categories: list[str],
    section: IndexedChunk,
    *,
    settings: ReviewSettings,
) -> list[str]:
    if not settings.section_classify_lexical_fallback:
        return categories
    if categories != ["general"]:
        return categories
    inferred = infer_categories_from_section(section)
    return normalize_categories(inferred) or categories
```

Call after **every** successful LLM normalize in `_classify_single_llm` and `classify_sections_batch` (3 call sites).

**Warning:** Append `; lexical_enriched={categories}` to `classify_warning` only when enrichment changed result (observability).

**Accuracy:** Only replaces `["general"]` — never overrides LLM when it returned a specific category.

---

## 5. LLM prompt taxonomy (minimal, accuracy helper)

**File:** `review_agent/prompts/section_policy_classify.md` (~8 lines)

Add rows to category table (LLM path when it works):

| Tag | What it covers |
|-----|----------------|
| `human_rights` | Forced labor, trafficking, UN Guiding Principles, supplier human-rights due diligence |
| `labor` | Working conditions, wages, child labor, recruitment agencies |
| `minerals` | Conflict minerals, MRT, RMAP, smelter/refiner sourcing |
| `environment` | GHG, CDP reporting, emissions targets, climate |
| `sustainability` | Circular design, sustainability reporting beyond bare legal compliance |
| `compliance` | Supplier code of conduct, RBA, audit/SAQ obligations |

Clarify: **`hr`** = internal employee HR; **`human_rights`** = supply-chain / vendor human-rights policies.

---

## 6. Files touched (minimal)

| File | Change | Est. lines |
|------|--------|------------|
| `document_core/schemas/taxonomy.py` | Categories + aliases | +25 |
| `review_agent/services/section_category_lexical.py` | ESG patterns + mis-map fix | +30, −2 |
| `review_agent/services/section_classifier.py` | `_enrich_categories_from_lexical` | +20 |
| `review_agent/prompts/section_policy_classify.md` | Table rows | +8 |
| `document_core/tests/test_taxonomy.py` | Alias tests | +15 |
| `review_agent/tests/test_section_category_lexical.py` | Cisco section fixtures | +45 |
| `review_agent/tests/test_section_classifier.py` | LLM returns general → enriched | +25 |

**Total:** ~170 lines. **No new files.** **No config flags** (reuse `section_classify_lexical_fallback=true`).

---

## 7. Code to remove / not add

| Item | Action |
|------|--------|
| `policy_category_hints.yaml` | **Do not create** — was planned in Phase 10, never shipped; lexical module replaces it |
| `classify_section_lexical` | Already deleted — no action |
| Duplicate keyword table in `contract_routing.py` | **Out of scope** — routing uses discovery phrases, not taxonomy tags; merging tables is P2 refactor |
| Broad `\blabor\b` / `\benvironment\b` patterns | **Do not add** — accuracy risk |

---

## 8. Tests (must pass before merge)

### 8.1 `test_section_category_lexical.py`

Use real Cisco section titles/text snippets:

```python
# §2 Human Rights and Labor
assert "human_rights" in infer(...) and "labor" in infer(...)

# §3 Responsible Minerals  
assert "minerals" in infer(...)

# §4 Environment and GHG
assert "environment" in infer(...)

# §1 Supplier Code of Conduct
assert "compliance" in infer(...)

# Definitions — still empty
assert infer(definitions_section) == []
```

### 8.2 `test_section_classifier.py`

```python
# LLM returns general; lexical enriches
async def test_classify_llm_general_enriched_to_minerals(monkeypatch):
    section = _section("Responsible Minerals", "MRT RMAP smelter...")
    # mock LLM → categories=["general"]
    assert "minerals" in result.categories
```

### 8.3 `test_taxonomy.py`

- ESG alias → environment
- forced_labor → human_rights

### 8.4 Regression

```powershell
cd Legal/review/review_agent
python -m pytest tests/test_section_category_lexical.py tests/test_section_classifier.py tests/test_taxonomy.py tests/test_multi_retrieval.py -q
```

---

## 9. Verification (E2E)

### 9.1 Cisco script (primary)

```powershell
cd Legal/temp_java_sync
python beta_test/run_cisco_assessment.py
```

**Pass criteria:**

| Metric | Before P0-C | After P0-C |
|--------|-------------|------------|
| Sections with policy hits | 4–5 / 6 | **6 / 6** |
| §2–§4 retrieve correct playbook | Often miss | **human_rights**, **minerals**, **environment** policies hit |
| `playbook_compare` findings | Partial | Violations with **correct section quotes** |
| Extra gap/final-verify LLM | 0–6 | **Same or fewer** |
| Score | Variable | **≥ prior best (10/10)** |

Check artifact / logs:

```json
"section_retrieval": { "s2": { "categories": ["human_rights", "labor"], "policy_hits": ">0" } }
```

### 9.2 Dev UI paste (11-section run)

After paste review, confirm classifier warnings show `lexical_fallback=` or `lexical_enriched=` for ESG sections — not bare `general` for § HR/minerals/environment blocks.

---

## 10. LLM call accounting

| Path | LLM impact |
|------|------------|
| Lexical inference | **0** (regex only) |
| `_enrich_categories_from_lexical` | **0** |
| Better first-pass retrieval | **−0 to 6** (avoids gap LLM + failed compare + guard on empty context) |
| Prompt table extension | **0** (same classify call count) |

**No increase** in classifier, compare, guard, or final-verify call counts.

---

## 11. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Over-tag compliance on generic “conduct” | Require `code of conduct` / `RBA` / `supplier` context |
| `environment` tag too broad | GHG/CDP/climate-specific regex only |
| Ingest uses `esg` string somewhere | Alias `esg` → `environment` in normalize |
| LLM + lexical disagree | Enrichment only when LLM said `general`; LLM wins when specific |

---

## 12. Implementation checklist

- [x] **P0-C.1** Taxonomy: add 6 categories + 8 aliases (`taxonomy.py`)
- [x] **P0-C.2** Lexical patterns: HR/minerals/ESG + mis-map cleanup (`section_category_lexical.py`)
- [x] **P0-C.3** Classifier enrichment when LLM returns `general` (`section_classifier.py`)
- [x] **P0-C.4** Prompt table rows (`section_policy_classify.md`)
- [x] **P0-C.5** Unit tests (lexical, classifier, taxonomy)
- [ ] **P0-C.6** Cisco assessment re-run + log category/hit verification
- [ ] **P0-C.7** Dev UI paste smoke (optional)

---

## 13. Phase sequence

```text
P0-A rate limit (done) → P0-B unclear cap (done) → P0-C lexical ESG (this) → P1 batched guard
```

**P0-C is safe to ship immediately after P0-B** — orthogonal to compare/guard; only improves retrieval inputs.

---

*Estimated implementation time: 1 focused session (~170 LOC + Cisco verification).*
