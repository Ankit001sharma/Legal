# Phase 22 P4 — Compare / LLM Quality (Covered Sections)

**Plan ID:** `DR-PHASE-22-P4-COMPARE-LLM-QUALITY`  
**Priority:** P4 (secondary — sections **with** policy context)  
**Impact:** **+0–1 LLM calls/contract** (schema repair only); **+15–25% gap recall** on covered sections; **−50% ungrounded/downgrade noise**  
**Depends on:** Phase 22 P1 (discovery scope), P2 (classifier), P3 (gap semantics), Phase 19 P2 (quote repair + guard), Phase 21 P1-Q (strict section grounding — **done**)  
**Scope:** `section_compare_llm.py`, `section_compare.py`, `quote_validate.py`, `multi_retrieval.py` (compare hit selection only), prompts, config, tests  
**Non-goals:** New graph nodes, discovery rewrite, cross-encoder reranker (see P2R plan), guard/rationale rewrite, Java API changes

---

## 0. Verified root cause (code + scale benchmark)

### Symptom → production impact

```text
Section HAS policy_hits (covered)
  → compare LLM runs with up to 10 policy blocks
  → wrong-family playbook cited OR weak clause marked COMPLIANT
  → paraphrased quotes → validate downgrades NC → INCONCLUSIVE
  → grounding repair too late (status already INCONCLUSIVE)
  → scale: gap_recall ~51% despite coverage ~57%
  → lawyer sees noise + missed violations on “reviewed” sections
```

**Enterprise 40+ context:** P1 widens discovery to **6–20 playbook families**. Compare still receives **multi-category policy soup** — tuned for Cisco **single-family** ESG runs where top-1 is usually correct.

### Evidence (scale 12×43, post P1+P3)

| Metric | Value | Implication |
|--------|-------|-------------|
| `avg_coverage_pct` | **57.1%** | Compare ran on ~11/20 sections |
| `avg_gap_recall_pct` | **51.1%** | Half of **labeled gap** sections not flagged NC/INC |
| `ungrounded_count` (sum 12 runs) | **~14** | Quotes fail substring gate → downgrade/drop |
| `grounding_downgraded_count` | non-zero | NC → INCONCLUSIVE after MCP verify |
| `guard_failed` | 3–4 / contract on some runs | Further INCONCLUSIVE on valid NC |
| Cisco §5 security | HR policy in compare (historical) | Wrong doc in multi-hit prompt |

Benchmark gap hit rule (`run_scale_benchmark.py` L56–58): counts only `NON_COMPLIANT` or `INCONCLUSIVE` with `source=playbook_compare`. **COMPLIANT**, **downgraded INCONCLUSIVE**, and **missing compare** = miss.

### Root causes (precise)

| # | Root cause | File / mechanism | Effect |
|---|------------|------------------|--------|
| **RC-1** | **All top-K hits sent to compare** | `multi_retrieval.py` L185–188 `retrieval_final_top_k=10`; `section_compare_llm.py` L90–108 loops **all** hits | Up to 10 policies from **different families** in one prompt; LLM picks wrong `policy_document_id` |
| **RC-2** | **No category alignment at compare** | `_format_sections_block` ignores `bundle.categories`; policy `IndexedChunk.categories` unused | Security section can receive HR + security + compliance blocks with equal weight |
| **RC-3** | **Compare-time quote downgrade before repair** | `quote_validate.py` L47–58; `section_compare_llm.py` L231–239 | NC with paraphrased quote → **immediate INCONCLUSIVE**; grounding quote repair never runs on original NC |
| **RC-4** | **`policy_quote` list not coerced** | `section_compare.py` L18–19 `policy_quote: str` — **no** `field_validator`; `contract_routing.py` has list coercion pattern but compare schema does not | Mistral returns `["text"]` → `ValidationError` → `compare_section_batch` except → `_failure_items` → section silence |
| **RC-5** | **Same for `contract_quote`** | Same schema gap | Batch failure or partial drop |
| **RC-6** | **Grounding default downgrades NC** | `nodes.py` L299–317 `grounding_downgrade_not_drop=True` + `grounding_downgrade_mode=inconclusive` | Repaired quote still loses NC if repair fails; `keep_status_flag` exists but **default off** |
| **RC-7** | **Weak-clause prompt bias** | `section_compare.md` L26 “Prioritize NC” but L36–44 allows easy COMPLIANT | Model marks “close enough” COMPLIANT on material deviations |
| **RC-8** | **Policy text lookup key mismatch** | `section_compare_llm.py` L229–230 `policy_key = section:doc:policy_section` | Wrong/missing policy_text → `policy_ok=True` when policy_quote empty → cross-section contract quote issues |

**Already fixed (do not re-implement in P4):**

| Fix | Status |
|-----|--------|
| Strict section-scoped MCP grounding | `grounding.py` L124–128 |
| Section mismatch cross-check | `grounding_quote.py` L14–26 |
| Quote repair at grounding | `grounding_quote.py` L81–120 |
| Rationale guard tiered (inference_ok / repair) | `guard_pass.py` |
| Status enum typos | `compliance_status_utils.py` |

---

## 1. Design principles (minimal production patch)

1. **Compare input quality > more LLM** — send **fewer, aligned** policy hits before changing prompts.
2. **Coerce at schema boundary** — never fail a whole batch on `list` quote drift (Mistral).
3. **Repair before downgrade** — deterministic quote anchoring at compare time; grounding repair as second pass.
4. **Preserve NC when legally justified** — `keep_status_flag` for grounded-failed NC after repair attempt (config, not default for all).
5. **0 new graph nodes** — filter hits in `compare_section_batch` / `section_compare_llm_node` prep.
6. **Feature flags** — rollback without revert (`compare_policy_hit_mode`, `quote_field_coerce`, etc.).
7. **Do not undo P1 discovery breadth** — narrow at **compare**, not retrieval/discovery.

---

## 2. Target behavior (after P4)

```text
section retrieval → bundle.policy_hits (top 10)
        │
        ▼
select_compare_hits(categories, mode)     [NEW — RC-1/2]
  → 1–3 hits: same category family as section classifier
        │
        ▼
compare_section_batch
  → schema coerces quote fields            [NEW — RC-4/5]
  → anchor_quotes before downgrade         [NEW — RC-3]
        │
        ▼
merge → grounding (repair) → guard
  → NC + repair_failed → keep_status_flag  [OPTION — RC-6]
```

**Lawyer-visible outcomes:**

| Before P4 | After P4 |
|-----------|----------|
| Security § compared to HR policy | Security § compared to **top security/MSS** hit |
| NC lost to INCONCLUSIVE (bad quote) | NC kept or INCONCLUSIVE **with repaired quote** |
| Batch fail on `policy_quote: []` | Coerced string; batch continues |
| gap_recall ~51% on covered | Target **≥65%** on scale corpus |

---

## 3. Implementation tasks

### P4-1. Quote field coercion (~35 lines)

**File:** `review_agent/schemas/quote_field_utils.py` (new, ~20 lines)

```python
def coerce_quote_field(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [str(x).strip() for x in value if str(x).strip()]
        return " ".join(parts)
    if isinstance(value, dict):
        for key in ("text", "quote", "content"):
            if key in value and value[key]:
                return str(value[key]).strip()
    return str(value).strip()
```

**Wire:** `field_validator("contract_quote", "policy_quote", mode="before")` on:

- `SectionCompareItem` (`section_compare.py`)
- `ComplianceLLMResult` / `BatchComplianceItem` (`compliance_llm.py`)
- `FinalGapVerifyItem` (contract_quote only)

**Acceptance:** `SectionCompareItem(section_id="1", status=..., rationale="...", policy_quote=["Vendor shall indemnify"])` validates.

---

### P4-2. Compare hit selection — category-aligned top-N (~55 lines)

**File:** `review_agent/services/compare_hit_selection.py` (new)

```python
ComparePolicyHitMode = Literal["all_top_k", "category_aligned", "primary_only"]

def select_compare_hits(
    hits: list[RetrievalHit],
    *,
    section_categories: list[str],
    settings: ReviewSettings,
) -> list[RetrievalHit]:
```

**Rules (deterministic, 0 LLM):**

| Mode | Behavior |
|------|----------|
| `primary_only` | `hits[:1]` after rerank |
| `category_aligned` **(default)** | Prefer hits where `parent.categories ∩ section_categories ≠ ∅`; else fall back to top-1; cap at `compare_max_policy_hits` (default **3**) |
| `all_top_k` | Legacy: `hits[:retrieval_final_top_k]` |

**Category source:** `SectionRetrievalBundle.categories` from classifier (already on bundle).

**Policy categories:** `hit.parent_chunk.metadata.get("categories")` (ingest P0-1).

**Wire:**

- `section_compare_llm.py` — in `compare_section_batch` / `_format_sections_block`, filter `hits_by_section[sid]` before format.
- `section_compare_llm_node` — optional warning: `"compare used N hits (mode=category_aligned) for §X"`.

**Config** (`config.py`):

```python
compare_policy_hit_mode: Literal["all_top_k", "category_aligned", "primary_only"] = "category_aligned"
compare_max_policy_hits: int = 3
```

**Prompt tweak** (`section_compare.md`, ~6 lines): When only Policy 1 present, compare **only** that document; do not invent requirements from other families.

---

### P4-3. Deterministic quote anchoring before compare downgrade (~45 lines)

**File:** `quote_validate.py`

Add `anchor_quote_in_haystack(candidate: str, haystack: str) -> str`:

1. If `quote_is_substring(candidate, haystack)` → return candidate.
2. Else token-window search: find longest span in haystack sharing ≥80% tokens with candidate (min 8 tokens).
3. Return anchored verbatim span or `""`.

**Change `validate_and_normalize_quotes`:**

```python
if not contract_ok:
    anchored = anchor_quote_in_haystack(result.contract_quote, contract_text)
    if anchored:
        contract_ok = True
        result = result.model_copy(update={"contract_quote": anchored})
# same for policy_quote
# Only downgrade NC/COMPLIANT if still not ok after anchor
```

**Why before grounding:** Compare-stage downgrade (RC-3) currently prevents NC from ever reaching quote repair with original intent.

**Acceptance:** Paraphrased liability cap → anchored span → status stays NC.

---

### P4-4. Policy text resolution hardening (~20 lines)

**File:** `section_compare_llm.py`

After `_backfill_policy_ids`:

1. Resolve `policy_text` via `_hit_lookup` (already exists L33–40) — **use lookup** instead of string key only.
2. If `policy_document_id` set but text empty, scan hits for matching doc id.
3. Run `_normalize_item_quotes` when `policy_text` non-empty **OR** when status is NC/COMPLIANT (contract quote always validated).

Fixes RC-8 empty policy_text → false `policy_ok`.

---

### P4-5. Compare batch partial retry on schema fail (~40 lines)

**Mirror P2 classifier pattern** (minimal):

In `compare_section_batch`, on `ValidationError` / structured parse fail when `len(sections) > 1` and `compare_batch_retry_single=True`:

- Retry each section as single-section batch (reuse gateway 429 backoff).

**Config:**

```python
compare_batch_retry_single: bool = True
```

**With P4-1 coercion**, retries should be rare — safety net only.

---

### P4-6. Grounding NC preservation (optional prod flag, ~5 lines default change)

**File:** `config.py`

Document and recommend for enterprise runs:

```python
# Default stays "inconclusive" for safety; scale/Cisco E2E use:
grounding_downgrade_mode: Literal["inconclusive", "keep_status_flag"] = "inconclusive"
```

**Optional P4-6b:** When `prior_status=NON_COMPLIANT` and `quote_repair_attempted` and contract quote repaired but policy failed → `keep_status_flag` automatically (narrow rule in `grounding_node`, ~15 lines).

**Do not** change global default without E2E sign-off.

---

### P4-7. Prompt — material deviation → NC (~12 lines)

**File:** `prompts/section_compare.md`

Add under Status values:

- When playbook `preferred_position` or policy text states a **numeric threshold, mandatory clause, or prohibited term**, and contract **materially deviates** → `NON_COMPLIANT`, not `COMPLIANT` or vague `INCONCLUSIVE`.
- “Silent” contract (no mention) on **mandatory** playbook requirement → `NON_COMPLIANT` or `INCONCLUSIVE` with explicit “contract silent on X” (feeds gap recall).

Cross-reference playbook hints block (P13 P4 — already wired).

---

### P4-8. Ops metadata (~15 lines)

**File:** `section_compare_nodes.py` stats / `report_node` metadata:

```python
"compare_hit_selection": {
    "mode": settings.compare_policy_hit_mode,
    "avg_hits_per_section": float,
    "category_aligned_sections": int,
    "fallback_primary_sections": int,
}
"compare_quote_anchored": int  # from batch stats
```

---

## 4. File touch list

| File | Change | Est. lines |
|------|--------|------------|
| `schemas/quote_field_utils.py` | **New** — coerce quotes | +25 |
| `schemas/section_compare.py` | Validators | +12 |
| `schemas/compliance_llm.py` | Validators | +8 |
| `services/compare_hit_selection.py` | **New** — hit filter | +55 |
| `services/section_compare_llm.py` | Selection, lookup, batch retry | +50 |
| `services/quote_validate.py` | `anchor_quote_in_haystack` | +45 |
| `config.py` + `.env.example` | 4 settings | +12 |
| `prompts/section_compare.md` | NC bias + single-policy note | +12 |
| `graph/section_compare_nodes.py` | Stats | +15 |
| `tests/test_quote_field_coerce.py` | **New** | +40 |
| `tests/test_compare_hit_selection.py` | **New** | +70 |
| `tests/test_quote_validate.py` | Anchor tests | +45 |
| `tests/test_section_compare.py` | Selection + schema | +50 |

**Total:** ~440 lines (incl. tests). **No graph topology change.**

---

## 5. Tests (must pass)

| Test | Setup | Assert |
|------|-------|--------|
| `test_coerce_policy_quote_list` | `policy_quote=["a","b"]` | Valid str `"a b"` |
| `test_coerce_policy_quote_dict` | `{"text": "clause"}` | `"clause"` |
| `test_select_hits_category_aligned` | Security categories + HR + security hits | Returns security hit first; max 3 |
| `test_select_hits_fallback_primary` | No category overlap | Top-1 by score |
| `test_primary_only_mode` | 5 hits | 1 hit |
| `test_anchor_paraphrased_quote` | Haystack with exact words out of order | Anchored substring; no downgrade |
| `test_validate_nc_kept_after_anchor` | NC + fixable quote | Status NC |
| `test_compare_batch_retry_single` | Mock batch schema fail | Per-section retry |
| `test_format_sections_single_policy` | primary_only | Prompt contains 1 Policy block |
| **Regression** | `test_compliance_status_normalize` | Enum typos still work |
| **Regression** | Cisco 6-section | 6/6 still pass |

---

## 6. Verification (E2E)

| Run | Before P4 | Target after P4 |
|-----|-----------|-----------------|
| Scale `avg_gap_recall_pct` | ~51% | **≥65%** |
| Scale sum `ungrounded_count` | ~14 | **≤5** |
| Scale `grounding_downgraded_count` | baseline | **−40%** |
| Wrong-policy compare (manual) | HR on security § | Security/MSS doc in finding |
| Compare LLM batches failed | occasional schema | **0** on scale run |
| `avg_elapsed_seconds` | ~146s | **≤155s** (+0–1 compare calls) |

```powershell
cd Legal\review\review_agent
python -m pytest tests/test_quote_field_coerce.py tests/test_compare_hit_selection.py tests/test_quote_validate.py tests/test_section_compare.py -q

cd Legal\temp_java_sync
python beta_test\run_cisco_assessment.py
python beta_test\run_scale_benchmark.py
```

**Acceptance query:**

```python
nc_downgraded = [
    f for f in findings
    if f.metadata.get("prior_status") == "NON_COMPLIANT"
    and f.status == INCONCLUSIVE
    and f.metadata.get("grounding_failed")
]
# Target: count drops vs baseline; gap_recall up
```

---

## 7. Rollout / risk

| Risk | Mitigation |
|------|------------|
| `category_aligned` drops correct cross-family policy | Fallback to top-1; `all_top_k` flag |
| `primary_only` misses multi-policy dimensions | Default `category_aligned` cap 3, not 1 |
| Quote anchor false positive | Require ≥80% token overlap + min length; unit tests |
| `keep_status_flag` shows ungrounded NC | UI already shows `grounded=false`; lawyer review |
| Over-aggressive NC prompt | Cisco E2E + false_nc_rate in scale benchmark |

**Rollback:** `COMPARE_POLICY_HIT_MODE=all_top_k`, `GAP_STATUS_*` unchanged, disable anchor via `compare_quote_anchor_enabled=false` (add flag if needed).

---

## 8. Implementation checklist

- [x] **P4-1** Quote field coercion
- [x] **P4-2** Category-aligned hit selection
- [x] **P4-3** Quote anchoring before downgrade
- [x] **P4-4** Policy text lookup fix
- [x] **P4-5** Compare batch single retry
- [x] **P4-6** Grounding NC preservation (flag + optional narrow rule) — default unchanged; `grounding_downgrade_mode` documented in config
- [x] **P4-7** Compare prompt NC bias
- [x] **P4-8** Ops metadata
- [x] **P4-9** Unit tests green (190/190 excl. e2e)
- [ ] **P4-10** Cisco + scale E2E

---

## 9. Out of scope (later)

| Issue | Phase |
|-------|-------|
| Cross-encoder reranker (retrieval rank) | P2R plan — complements P4-2 |
| Per-dimension multi-pass compare | P5 |
| Guard prompt tuning | P19 follow-up |
| Compare LLM model upgrade | Ops |
| Java policy `categories` backfill | Ingest/catalog |

**P4 completes Phase 22 quality stack:** P1 scope → P2 reachability → P3 verdict trust → **P4 compare precision on covered sections**.

---

## 10. Relationship to prior plans

| Plan | Overlap | P4 action |
|------|---------|-----------|
| Phase 21 P1-Q wrong-section quote | Grounding strict | **Done** — no P4 work |
| Phase 19 P2 verdict quality | Quote repair + guard | P4-3 anchors **earlier**; do not remove grounding repair |
| Phase 21 P2R reranker | Top-1 precision at retrieval | P4-2 fixes at compare; **both** recommended for enterprise |
| Phase 22 P3 silence trust | Gap rows | Orthogonal — P4 only **covered** sections |

---

*End of Phase 22 P4 plan — compare / LLM quality on covered sections.*
