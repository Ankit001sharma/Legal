# Phase 21 P2 — Lexical-First Section Classifier (LLM Fallback)

**Plan ID:** `DR-PHASE-21-P2-LEXICAL-FIRST-CLASSIFIER`  
**Priority:** P2  
**Impact:** **−4 to −6 classifier LLM calls** per typical 11-section review; **−0 to 2** downstream gap/compare calls when retrieval hits on first pass  
**Accuracy:** ★★★★★ — same or better retrieval categories; LLM retained for ambiguous/boilerplate sections  
**Depends on:** P0-A (rate limit), P0-C (ESG lexical patterns + enrichment) — both implemented  
**Scope:** `section_category_lexical.py`, `section_classifier.py`, `config.py`, tests  
**Non-goals:** New graph nodes, routing YAML merge, Java/MCP changes, replacing compare/guard LLM, training a new ML model

---

## 0. Problem (root cause — verified in code)

**Today:** Every review section hits the **LLM classifier first**, regardless of how obvious the title is.

```text
classify_all_sections (11 sections, batch_size=2)
  → 6 LLM batch calls (2 concurrent waves)
  → on success: categories from LLM
  → on failure OR general-only: lexical patch (_fallback_result / _enrich_categories_from_lexical)
```

### Why this hurts production

| Symptom | Cause |
|---------|--------|
| 429 / rate-limit pressure | Classifier is **pure overhead** on titled clauses (`Limitation of Liability`, `Human Rights and Labor`) |
| ~6 classify LLM calls per Cisco run | `section_classify_batch_size=2` × 11 sections |
| ESG sections still hit LLM before lexical helps | P0-C enrichment runs **after** LLM returns `general` — LLM cost already paid |
| `query_terms` on lexical-only paths are weak | `_section_query()` uses **contract** language, not policy retrieval phrases |

### What P0-C fixed vs what P2 fixes

| Layer | P0-C (done) | P2 (this plan) |
|-------|-------------|----------------|
| Lexical vocabulary | ESG + commercial patterns | **Reuse** — no duplicate table |
| When lexical runs | After LLM fail or `general` | **Before** LLM when confident |
| LLM call count | Unchanged | **Skip** for obvious sections |
| Query terms | Title snippet only | **Category-aligned** retrieval phrases |

**Accuracy rule:** Lexical-first must **never** skip LLM when lexical returns `[]` or only untrusted weak signals. LLM remains the authority for definitions, misc, and multi-topic ambiguity.

---

## 1. Design principles

1. **Lexical-first, LLM-fallback** — mirror `contract_routing_mode` pattern already in `config.py`.
2. **Fail open to LLM** — when in doubt, call LLM (accuracy > savings).
3. **Minimal diff** — extend `section_category_lexical.py`; refactor `section_classifier.py` (~80 LOC net); **no new modules**.
4. **One partition function** — unify single-section and batch paths (remove `len(sections)==1` early LLM branch).
5. **Defense in depth** — keep `_enrich_categories_from_lexical` on LLM `general` responses; keep `_fallback_result` lexical on LLM errors.
6. **Remove dead paths** — delete redundant single-only flow; update module docstring; collapse duplicate flags.
7. **0 new MCP / ingest work** — categories must still match `normalize_categories()` + ingest metadata (P0-C).

---

## 2. Target flow (after P2)

```text
classify_all_sections(sections)
  │
  ├─ FOR EACH section (sync, 0 LLM):
  │     infer_lexical_classify(section)
  │       ├─ confidence=high  → SectionCategoryResult (skip LLM)
  │       └─ confidence=low   → queue for LLM batch
  │
  ├─ IF queue non-empty:
  │     classify_sections_batch_llm(queue)   # existing prompt, batched
  │       └─ on item: merge + _enrich if LLM returned general
  │
  └─ merge lexical + LLM results by section_id
```

### LLM call accounting (11-section Cisco)

| Mode | Classify LLM calls | Notes |
|------|-------------------|--------|
| Today (`llm` implicit) | **6** | 11 ÷ 2 batched |
| After P2 (`lexical_first`) | **0–2** | ~9 titled commercial/ESG sections skip; Definitions + Misc → 1 batch |
| Worst case (all ambiguous) | **6** | Same as today — no regression |

---

## 3. Confidence model (accuracy-critical)

**File:** `section_category_lexical.py` (+35 lines)

Add a small result type (dataclass or TypedDict — keep in same file):

```python
@dataclass(frozen=True)
class LexicalClassifyResult:
    categories: list[str]
    confidence: Literal["title", "body", "none"]
    matched_via: str  # "title" | "body" | ""
```

### 3.1 `infer_lexical_classify(section) -> LexicalClassifyResult`

Reuse existing `_scan_text` / `_CATEGORY_KEYWORDS` — **do not duplicate patterns**.

| Step | Logic |
|------|--------|
| 1 | `from_title = _scan_text(title, title_priority=True)` |
| 2 | If `from_title` non-empty → `confidence="title"`, categories=`normalize(from_title)` |
| 3 | Else `from_body = _scan_text(f"{title} {body[:200]}", title_priority=False)` |
| 4 | If `from_body` non-empty → `confidence="body"`, categories=`normalize(from_body)` |
| 5 | Else → `confidence="none"`, categories=`[]` |

Refactor `infer_categories_from_section()` to call `infer_lexical_classify(...).categories` — **one code path**, no drift.

### 3.2 Skip-LLM gate (conservative v1)

**Skip LLM when ALL true:**

```python
cfg.section_classify_mode == "lexical_first"
and result.confidence in ("title", "body")
and len(result.categories) >= 1
and result.categories != ["general"]   # lexical never emits general today; guard for future
```

**Always call LLM when:**

- `confidence == "none"` (Definitions, Notices, Miscellaneous, novel titles)
- `section_classify_mode == "llm_only"` (debug / A-B)
- Section text shorter than `review_min_section_chars` (already filtered upstream — no change)

**Do NOT add** a third “weak body only → force LLM” rule in v1 — P0-C patterns are specific; over-calling LLM defeats P2. Revisit only if Cisco shows body-only false positives.

### 3.3 Acceptance (confidence)

- [x] `"Human Rights and Labor"` → `confidence=title`, categories include `human_rights`
- [x] `"Responsible Minerals"` → `confidence=title`, `minerals`
- [x] `"Definitions"` → `confidence=none`, `[]` → **LLM required**
- [x] Untitled body-only liability mention → `confidence=body`, `liability` → skip LLM (acceptable v1)

---

## 4. Query terms for lexical path (retrieval accuracy)

**Problem:** `_section_query(section)` produces contract snippets (`"The total liability shall not..."`). Retrieval expects **policy-topic phrases** (see `section_policy_classify.md` query rules).

**File:** `section_category_lexical.py` (+25 lines)

Add compact map — **primary category → 1–2 policy phrases** (aligned with `routing_topic_hints.yaml` + prompt table):

```python
_CATEGORY_QUERY_TERMS: dict[str, tuple[str, ...]] = {
    "liability": ("limitation of liability cap",),
    "indemnity": ("indemnification obligations",),
    "confidentiality": ("confidential information",),
    "human_rights": ("forced labor human rights", "supplier human rights"),
    "labor": ("working conditions labor standards",),
    "minerals": ("conflict minerals MRT RMAP",),
    "environment": ("greenhouse gas emissions CDP",),
    "sustainability": ("sustainability reporting",),
    "compliance": ("supplier code of conduct",),
    "privacy": ("data protection personal data",),
    "security": ("information security controls",),
    # ... one entry per STANDARD_POLICY_CATEGORIES member that lexical can emit
}
```

```python
def infer_query_terms_from_lexical(
    categories: list[str],
    section: IndexedChunk,
) -> list[str]:
    terms: list[str] = []
    for cat in categories[:3]:
        for phrase in _CATEGORY_QUERY_TERMS.get(cat, ()):
            if phrase not in terms:
                terms.append(phrase)
            if len(terms) >= 3:
                return terms
    # Fallback: title-only (better than contract body snippet)
    title = (section.title or "").strip()
    return [title] if title else [_section_query(section)]
```

Wire into lexical-first `SectionCategoryResult.query_terms`.

### Acceptance (query terms)

- [ ] Lexical liability section → `query_terms[0]` contains `"limitation of liability"` (policy phrase, not contract sentence)
- [ ] Lexical minerals section → terms mention `MRT` or `minerals`
- [ ] multi_retrieval attempt-0 uses first term (existing behavior)

---

## 5. Classifier refactor (core change)

**File:** `section_classifier.py` (~60 lines changed, ~25 removed)

### 5.1 Config flag (replace boolean)

**File:** `config.py`

```python
# Remove (after migration):
# section_classify_lexical_fallback: bool = True

# Add:
section_classify_mode: Literal["lexical_first", "llm_only"] = "lexical_first"
```

| Mode | Behavior |
|------|----------|
| `lexical_first` (default) | Skip LLM when lexical confident; LLM for remainder; lexical on LLM fail |
| `llm_only` | Current always-LLM behavior (no skip); keep enrich + fallback for parity testing |

**Env:** `SECTION_CLASSIFY_MODE=lexical_first`

`.env.example`: document new var; note deprecated `SECTION_CLASSIFY_LEXICAL_FALLBACK`.

### 5.2 New helpers

```python
def _lexical_classify_result(
    section: IndexedChunk,
    *,
    settings: ReviewSettings,
) -> SectionCategoryResult | None:
    """Return full result if LLM can be skipped; None if LLM required."""
    if settings.section_classify_mode != "lexical_first":
        return None
    lex = infer_lexical_classify(section)
    if lex.confidence == "none" or not lex.categories:
        return None
    return SectionCategoryResult(
        section_id=section.section_id,
        categories=lex.categories,
        query_terms=infer_query_terms_from_lexical(lex.categories, section),
        classify_warning=f"lexical_first={lex.confidence}:{lex.categories}",
    )
```

### 5.3 Unified batch entry

**Replace** `classify_sections_batch` branching (`len==1` → `_classify_single_llm`):

```python
async def classify_sections_batch(sections, ...):
    cfg = settings or get_settings()
    if not sections:
        return {}

    out: dict[str, SectionCategoryResult] = {}
    needs_llm: list[IndexedChunk] = []

    for section in sections:
        lexical = _lexical_classify_result(section, settings=cfg)
        if lexical is not None:
            out[section.section_id] = lexical
        else:
            needs_llm.append(section)

    if needs_llm:
        llm_out = await _classify_batch_llm(needs_llm, ...)  # rename from inline batch body
        out.update(llm_out)

    return out
```

Extract existing batch LLM body + `_classify_single_llm` into **`_classify_batch_llm`** — single implementation for 1..N sections (batch prompt when N>1, single USER template when N==1 **or** always use batch format with one block — prefer **one batch path** to delete `_classify_single_llm` entirely).

### 5.4 Keep LLM post-processors (do not remove)

| Helper | When | Why keep |
|--------|------|----------|
| `_enrich_categories_from_lexical` | LLM returned `["general"]` | Catches conservative LLM on ESG titles |
| `_fallback_result` | LLM exception / omitted section | 429-safe path; uses lexical |

Update `_enrich_categories_from_lexical` guard:

```python
if settings.section_classify_mode == "llm_only" and not settings.section_classify_lexical_fallback:
    # After flag removal: only skip enrich in llm_only with env SECTION_CLASSIFY_LEXICAL_ENRICH=false (optional)
```

**Simpler:** always allow enrich + fallback in both modes — they cost 0 LLM.

### 5.5 Code to remove

| Item | Action |
|------|--------|
| `_classify_single_llm()` | **Delete** — merged into `_classify_batch_llm` |
| `len(sections) == 1` early return | **Delete** |
| Module docstring `"LLM only"` | **Fix** → `"Lexical-first with LLM fallback"` |
| `section_classify_lexical_fallback` | **Remove** from config/tests; migration note in `.env.example` |
| Duplicate lexical inference in `_fallback_result` | **Keep** — different trigger (error path) |

**Do not remove** `_enrich_categories_from_lexical` — still needed when LLM runs and returns `general`.

---

## 6. Observability

**`classify_warning` values (grep-friendly):**

| Value | Meaning |
|-------|---------|
| `lexical_first=title:[...]` | Skipped LLM — title match |
| `lexical_first=body:[...]` | Skipped LLM — body match |
| `lexical_enriched=[...]` | LLM said general; lexical upgraded |
| `...; lexical_fallback=[...]` | LLM failed; lexical used |

**Optional P2.7 (+8 lines):** increment `compliance_stats["classify_lexical_skipped"]` in `section_policy_retrieval_node` — count warnings starting with `lexical_first=`. Skip if minimizing diff.

---

## 7. Files touched (minimal)

| File | Change | Est. lines |
|------|--------|------------|
| `services/section_category_lexical.py` | `LexicalClassifyResult`, query-term map, refactor infer | +55 |
| `services/section_classifier.py` | Partition + unified batch LLM; delete single path | +40, −45 |
| `config.py` | `section_classify_mode`; remove old bool | +3, −1 |
| `.env.example` | Document `SECTION_CLASSIFY_MODE` | +3 |
| `tests/test_section_category_lexical.py` | Confidence + query terms | +35 |
| `tests/test_section_classifier.py` | Skip LLM, llm_only mode, batch partial | +50, −10 |
| `tests/test_config.py` | Default mode | +3, −3 |
| `graph/section_retrieval_nodes.py` | Optional stats counter | +0–8 |

**Total:** ~170 lines. **No new files.**

---

## 8. Tests

### 8.1 `test_section_category_lexical.py`

| Test | Assert |
|------|--------|
| `test_lexical_confidence_title_hr` | Cisco §2 → `confidence=title`, `human_rights` in categories |
| `test_lexical_confidence_none_definitions` | Definitions → `none`, `[]` |
| `test_query_terms_liability_policy_phrase` | terms[0] matches policy phrase map |
| `test_infer_categories_backward_compat` | wrapper still returns same list as before |

### 8.2 `test_section_classifier.py`

| Test | Assert |
|------|--------|
| `test_lexical_first_skips_llm_liability` | `invoke_structured` **not called**; `liability` in categories; `lexical_first=title` |
| `test_lexical_first_llm_for_definitions` | LLM **called once**; mock returns categories |
| `test_lexical_first_batch_mixed` | 2 sections: titled liability skips, Definitions calls LLM — 1 invoke |
| `test_llm_only_always_calls_llm` | mode=`llm_only`; liability section still invokes LLM |
| `test_llm_general_still_enriched` | LLM returns general + minerals title → enriched (regression) |
| `test_classify_failure_still_lexical_fallback` | LLM throws → liability from lexical |

Update `test_classify_lexical_fallback_disabled` → `test_llm_only_with_llm_failure` or remove if redundant.

### 8.3 Regression suite

```powershell
cd Legal\review\review_agent
python -m pytest tests/test_section_classifier.py tests/test_section_category_lexical.py tests/test_multi_retrieval.py tests/test_section_retrieval_warnings.py -q
python -m pytest tests/ -q --ignore=tests/test_review_e2e.py
```

---

## 9. E2E verification

### 9.1 Cisco assessment

```powershell
cd Legal\temp_java_sync
python beta_test/run_cisco_assessment.py
```

| Check | Before P2 | After P2 |
|-------|-----------|----------|
| Legal score | 10/10 | **≥ 10/10** (same violations) |
| Sections with policy hits | 6/6 | **6/6** |
| Classifier LLM calls (log) | ~3 batches / 6 sections | **≤ 2 calls** |
| §2–§4 categories | human_rights, minerals, environment | **Same tags** |
| 429 in classify path | possible | **none** |
| Wall-clock | ~163s | **−15 to −40s** (estimate) |

### 9.2 Dev UI (11-section paste)

- Warnings include `lexical_first=title:` for obvious clause headings
- No section with titled `"Limitation of Liability"` should show LLM-only path
- Retrieval bundles: same or more `policy_hits` per section

### 9.3 Spot check (accuracy)

For each section in artifact:

```python
assert classification.categories  # non-empty
# If lexical_first: categories match infer_lexical_classify(section).categories
# If LLM path: categories non-general OR enriched
```

---

## 10. LLM call accounting (full pipeline)

| Stage | P2 impact |
|-------|-----------|
| Section classify | **−4 to −6 calls** typical |
| multi_retrieve | 0 (same queries, better terms on lexical path) |
| section_compare | **−0 to 2** (fewer empty-retrieval gaps) |
| guard / final-verify | **−0 to 1** (indirect) |

**Net:** fewer calls, same or better retrieval — **accuracy must not drop**.

---

## 11. Risk matrix

| Risk | Mitigation |
|------|------------|
| Skip LLM on section needing subtle multi-category tags | Body scan returns up to 3 categories; LLM runs when lexical empty |
| Weak query terms hurt retrieval | `_CATEGORY_QUERY_TERMS` map + title fallback |
| `llm_only` regression for A/B | Keep mode flag; tests for both |
| Flag migration breaks `.env` | Accept deprecated `SECTION_CLASSIFY_LEXICAL_FALLBACK=true` as alias → `lexical_first` for one release (optional 3-line compat in config validator) |
| Duplicate keyword tables with `contract_routing.py` | **Out of scope** — different outputs (topics vs taxonomy tags); note in backlog |
| Batch size 2 with 1 LLM section | `_classify_batch_llm` handles N=1 — no special case |

---

## 12. Implementation checklist

- [x] **P2.1** `LexicalClassifyResult` + `infer_lexical_classify()` refactor (`section_category_lexical.py`)
- [x] **P2.2** `_CATEGORY_QUERY_TERMS` + `infer_query_terms_from_lexical()`
- [x] **P2.3** `_lexical_classify_result()` + partition in `classify_sections_batch`
- [x] **P2.4** Merge `_classify_single_llm` → `_classify_batch_llm`; delete dead branch
- [x] **P2.5** `section_classify_mode` config; remove `section_classify_lexical_fallback`
- [x] **P2.6** Unit tests (lexical confidence, skip-LLM, mixed batch, regressions)
- [ ] **P2.7** Cisco / Dev UI re-run — score + hit rate unchanged or better
- [ ] **P2.8** Optional: `classify_lexical_skipped` in `compliance_stats`

---

## 13. Phase 21 sequence

```text
P0-A rate limit ✅ → P0-B final-verify cap ✅ → P0-C lexical ESG ✅
  → P1 guard batch ✅ → P1 dedupe/cap ✅ → P1 wrong-section quote ✅
  → **P2 lexical-first classifier (this)** → future: routing/category table dedupe
```

**Orthogonal to** quote grounding and dedupe — safe to ship independently once P1 wrong-section quote E2E is green.

---

## 14. Before / after diagram

```text
TODAY (LLM-first)
─────────────────
§2 Human Rights ──► LLM batch ──► general? ──► lexical enrich ──► retrieve
§3 Minerals     ──► LLM batch ──► general? ──► lexical enrich ──► retrieve
§9 Definitions  ──► LLM batch ──► general    ──► retrieve (weak)

AFTER P2 (lexical-first)
────────────────────────
§2 Human Rights ──► lexical (title) ──► SKIP LLM ──► retrieve ✓
§3 Minerals     ──► lexical (title) ──► SKIP LLM ──► retrieve ✓
§9 Definitions  ──► lexical (none)  ──► LLM batch ──► retrieve
```

---

*End of Phase 21 P2 plan — lexical-first classifier with LLM fallback.*
