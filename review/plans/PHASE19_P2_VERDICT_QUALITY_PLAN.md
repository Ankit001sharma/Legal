# Phase 19 — P2 Verdict Quality (Guard + Grounding)

**Plan ID:** `DR-PHASE-19-P2`  
**Owner:** Youngser  
**Scope:** `review_agent` — post-compare trust layer only (`guard_pass`, `quote_validate`, `grounding_node`)  
**Goal:** Stop **valid NON_COMPLIANT** findings from being downgraded to **INCONCLUSIVE** due to over-strict quote/rationale checks.  
**Depends on:** Phase 17 P0 (retrieval), Phase 18 P1 (classifier + enum typos)  
**Estimate:** ~280 lines prod + ~200 lines tests + 1 sprint day  
**Status:** Implemented

---

## 0. Executive summary

Two P2 defects weaken **verdict quality** after the pipeline already produces correct legal analysis:

| ID | Defect | Symptom | Impact |
|----|--------|---------|--------|
| **P2-6** | Rationale guard too strict | `guard_failed: 1` → status forced to `INCONCLUSIVE` | Clear **NON_COMPLIANT** hidden behind weak verdict |
| **P2-7** | Quote grounding too brittle | `grounded: false`, `ungrounded_count: 1` | Findings lose trust flag; chained guard skip/fail |

**Youngser solution:** Replace binary fail-closed downgrades with **LLM repair passes** and **tiered trust outcomes** — no new regex/rule engines, no fuzzy-threshold hacks. Keep MCP `verify_quote` as the final verbatim gate after repair.

**Design constraint (non-negotiable):** Dynamic LLM only for judgment/repair. Deterministic substring check remains the **final** integrity gate — not the first and only gate.

---

## 1. P2 bug register

### P2-6 — Rationale guard over-downgrades to INCONCLUSIVE

#### Finding (observed)

Beta / E2E liability section:

```
status: NON_COMPLIANT (compare LLM)
rationale: "... $100,000 is extremely low ... materially unfavorable ..."
guard_failed: true
guard_reason: "subjective assessments not explicitly supported by quotes"
final status: INCONCLUSIVE
```

`guard_checked: 7`, `guard_failed: 2` in recent beta runs.

#### Root cause (precise)

| Layer | Issue |
|-------|-------|
| **Prompt** | `rationale_guard.md` L7: *"without adding facts not present in the quotes"* — interpreted by guard LLM as **forbidding all legal evaluation** (low, unfavorable, nominal) |
| **Schema** | `RationaleGuardResult.supported: bool` — no distinction between **hallucination** vs **professional inference from quoted facts** |
| **Downgrade policy** | `guard_pass.py` L86–92: any `supported=false` → hard `INCONCLUSIVE` + `grounded=false` — same severity as compare failure |
| **Context starvation** | Guard sees truncated quotes (800 chars) + rationale only — no playbook `review_guidance`, no policy dimension label, no compare status context |
| **Ordering** | Guard runs **after** grounding; failed guard clears `grounded` even when MCP quote verify passed |

**Classification:** Trust-layer **policy bug** — guard prompt + response schema, not compare LLM quality.

#### Youngser solution (optimal, non-deterministic)

Three-part fix — **no keyword allowlists, no regex “legal word” lists**:

1. **Prompt v2** — Explicitly allow *evaluative legal inference* when every factual claim is traceable to quotes; reject only **new facts**, **contradictions**, or **unsupported policy requirements**.
2. **Tiered guard schema** — Replace bool with `support_level: FULL | INFERENCE_OK | UNSUPPORTED` — downgrade only on `UNSUPPORTED`.
3. **Rationale repair pass (LLM)** — On `UNSUPPORTED`, one repair attempt: rewrite rationale to quote-anchored language preserving status; re-guard once. Downgrade only if repair also fails.

```text
compare finding (NON_COMPLIANT, quotes MCP-grounded)
    → guard_pass (tiered)
        → FULL / INFERENCE_OK → keep status
        → UNSUPPORTED → repair_rationale_llm (1 retry)
            → re-guard → keep or downgrade
```

---

### P2-7 — Quote grounding downgrade path

#### Finding (observed)

```
ungrounded_count: 1
contract_quote: "the total liability ... $100,000 ... consequential damages"  (slightly paraphrased)
grounding_failed: true → INCONCLUSIVE
```

Compare-stage `validate_and_normalize_quotes` may pass (substring on section batch text), but **MCP `verify_quote`** fails when:

- LLM elides words, changes punctuation, or merges sentences
- Section scoped to `section_id` but quote spans canonical boundary
- Whitespace normalization insufficient for `$100,000` vs `$100,000.` variants

#### Root cause (precise)

| Layer | Issue |
|-------|-------|
| **Pre-compare** | `quote_validate.quote_is_substring` — exact normalized substring only (`quote_validate.py` L23–31) |
| **Post-compare** | `grounding_node` calls MCP `verify_quote` with **raw LLM quote** — no repair attempt (`nodes.py` L234–242) |
| **Failure policy** | `grounding_downgrade_not_drop=True` → immediate `INCONCLUSIVE` (`nodes.py` L258–272) |
| **No second chance** | Unlike retrieval retry ladder, grounding has **zero** LLM repair attempt |
| **document_core** | `verify_quote` uses normalized substring on haystack (`grounding.py` L61) — correct as **verifier**, wrong as **only** strategy |

**Classification:** Missing **LLM quote repair** step before verbatim verification — not a broken MCP service.

#### Youngser solution (optimal, non-deterministic)

**Quote repair pass (LLM)** inserted in grounding pipeline **before** MCP verify:

```text
finding with contract_quote / policy_quote
    → repair_quote_llm(source_section_text, candidate_quote)
        → returns verbatim substring from source OR empty
    → MCP verify_quote(repaired_quote)
        → grounded=true → keep status + quotes
        → still fail → tiered outcome (see §4.2)
```

- Repair LLM must **select/copy exact text** from provided section — not paraphrase.
- MCP verify remains deterministic final gate (integrity, not judgment).
- **No** Levenshtein/fuzzy match as primary fix (avoids false positives on legal text).

---

## 2. Design principles (production-grade)

1. **Dynamic repair, deterministic verify** — LLM fixes quotes/rationale; MCP/substring confirms verbatim integrity.
2. **Tiered trust, not binary drop** — Distinguish `INFERENCE_OK` (keep status) from `UNSUPPORTED` (downgrade).
3. **One repair retry max** — Bound latency/cost; log repair attempts in artifact ops.
4. **No rule engine** — Do not add YAML thresholds, banned-word lists, or regex compliance rules (aligned with P4/P6).
5. **Minimal graph change** — Extend `grounding_node`; extend `run_guard_pass`; no new graph nodes.
6. **Fail safe on true hallucination** — Downgrade when repair cannot produce MCP-grounded quotes.

---

## 3. Target pipeline (after P2)

```text
final_gap_verify
    → grounding_node
        1. enrich policy titles (existing)
        2. repair_quotes_llm (NEW — P2-7) per finding
        3. MCP verify_quote (existing — final gate)
        4. tiered grounding outcome (NEW — P2-7)
        5. guard_pass tiered (NEW — P2-6)
        6. optional rationale repair + re-guard (NEW — P2-6)
        7. section coverage backfill (existing)
    → report
```

---

## 4. Implementation plan — task breakdown

### Sprint order

```text
P2-7.1 quote repair module + schema
    → P2-7.2 wire into grounding_node
        → P2-6.1 guard prompt + tiered schema
            → P2-6.2 rationale repair pass
                → P2-6.3 config + artifact ops
                    → tests + beta gates
```

---

## 5. P2-7 — Quote grounding repair (Youngser)

### Task P2-7.1 — Quote repair LLM module

**File:** `review_agent/services/quote_repair_llm.py` (new, ~90 lines)

**Schema:** `review_agent/schemas/quote_repair.py` (new)

```python
class QuoteRepairResult(BaseModel):
    repaired_quote: str = ""          # verbatim substring from source, or empty
    confidence: float | None = None   # model self-score 0-1
    repair_notes: str = ""            # short debug string for artifact
```

**Prompt:** `review_agent/prompts/quote_repair.md` (new)

Rules for repair LLM:

- Input: full section text (truncated to config budget) + candidate quote from compare LLM
- Output: **exact contiguous substring** from section that best supports the candidate meaning
- If no faithful substring exists, return empty string
- Do not invent, paraphrase, or normalize currency/format

**Function:**

```python
async def repair_quote_for_section(
    *,
    source_text: str,
    candidate_quote: str,
    section_id: str,
    settings: ReviewSettings,
) -> QuoteRepairResult
```

**Acceptance:**

- [ ] Paraphrased liability quote → repaired verbatim span from section text
- [ ] Fabricated quote → empty `repaired_quote`
- [ ] Repaired quote passes `quote_is_substring(repaired, source_text)`

---

### Task P2-7.2 — Tiered grounding outcomes

**File:** `review_agent/graph/nodes.py` — `grounding_node`

Replace binary downgrade block (L255–272) with:

| MCP result | Action |
|------------|--------|
| `grounded=true` (original or repaired) | `grounded=true`, keep status |
| Repair attempted + MCP fail | `metadata.grounding_repair_attempted=true`; downgrade per config |
| No quotes | skip (existing) |

**Config** (`review_agent/config.py`):

```python
quote_repair_enabled: bool = True
quote_repair_max_chars: int = 8_000
grounding_downgrade_mode: Literal["inconclusive", "keep_status_flag"] = "inconclusive"
```

`keep_status_flag` mode: keep `NON_COMPLIANT` but set `grounded=false` + warning (lawyer sees flag, not silent downgrade) — **optional**, default stays safe.

**Acceptance:**

- [ ] Paraphrased quote repaired → MCP passes → status unchanged
- [ ] `metadata.quote_repair_used=true` when repair succeeds
- [ ] `ops.quote_repair_attempts` / `ops.quote_repair_success` in artifact

---

### Task P2-7.3 — Pre-compare soft path (optional, same sprint)

**File:** `review_agent/services/quote_validate.py`

When `validate_and_normalize_quotes` would downgrade for substring fail:

- Do **not** downgrade immediately if `quote_repair_enabled`
- Return item with `metadata.needs_quote_repair=true` for grounding stage

*Alternative (simpler):* repair only in `grounding_node` — **recommended for v1** to keep compare path unchanged.

**Acceptance v1:** Repair runs only in grounding_node.

---

### Task P2-7.4 — Tests

**File:** `review_agent/tests/test_quote_repair_llm.py` (new, ~80 lines)

| Test | Scenario |
|------|----------|
| `test_repair_finds_verbatim_span` | Mock LLM returns exact substring |
| `test_grounding_node_uses_repair_before_verify` | Mock repair + MCP |
| `test_no_repair_when_already_grounded` | MCP pass on first quote |
| `test_downgrade_when_repair_and_verify_fail` | INCONCLUSIVE path |

---

## 6. P2-6 — Rationale guard refinement (Youngser)

### Task P2-6.1 — Tiered guard schema + prompt v2

**File:** `review_agent/schemas/guard_llm.py` (new)

```python
class SupportLevel(str, Enum):
    FULL = "FULL"                   # rationale fully quote-supported
    INFERENCE_OK = "INFERENCE_OK"   # evaluative legal judgment from quoted facts
    UNSUPPORTED = "UNSUPPORTED"     # new facts, contradiction, hallucination

class RationaleGuardResult(BaseModel):
    support_level: SupportLevel
    reason: str = Field(default="", max_length=500)
```

**File:** `review_agent/prompts/rationale_guard.md` — rewrite

Key instructions:

- **ALLOW:** comparative/evaluative language (*low*, *unfavorable*, *below policy standard*) when tied to quoted numeric/terms differences
- **REJECT:** new parties, dates, dollar amounts, or policy obligations **not** in quotes
- **REJECT:** rationale contradicts quotes
- Output `INFERENCE_OK` for professional legal judgment grounded in quoted diffs (e.g. fixed cap vs fees-based cap)

**File:** `review_agent/services/guard_pass.py`

```python
if result.support_level in (SupportLevel.FULL, SupportLevel.INFERENCE_OK):
    meta["guard_support_level"] = result.support_level.value
    return finding, "checked"
# only UNSUPPORTED triggers repair/downgrade path
```

**Acceptance:**

- [ ] Liability $100k vs fees-cap scenario → `INFERENCE_OK` or `FULL`, status stays `NON_COMPLIANT`
- [ ] Rationale citing "$5M cap" when quotes say $100k → `UNSUPPORTED`

---

### Task P2-6.2 — Rationale repair pass (LLM)

**File:** `review_agent/services/rationale_repair_llm.py` (new, ~70 lines)  
**Prompt:** `review_agent/prompts/rationale_repair.md` (new)

On `UNSUPPORTED` only:

1. LLM rewrites rationale using **only** words/concepts from quotes
2. Re-run guard once
3. If still `UNSUPPORTED` → downgrade to `INCONCLUSIVE` (existing behavior)

```python
class RationaleRepairResult(BaseModel):
    rationale: str = Field(..., min_length=5)
```

**Config:**

```python
guard_rationale_repair_enabled: bool = True
guard_pass_max_tokens: int = 512
```

**Acceptance:**

- [ ] First guard UNSUPPORTED + repair success → status preserved
- [ ] `metadata.guard_repair_attempted=true` logged
- [ ] Max one repair per finding

---

### Task P2-6.3 — Guard context enrichment

**File:** `guard_pass.py` — extend `user_tpl` inputs:

| Field | Source |
|-------|--------|
| `dimension_label` | finding |
| `playbook_guidance` | `finding.metadata.review_guidance` or playbook hints |
| `prior_compare_rationale` | finding.rationale (before repair) |

No new MCP calls — use metadata already on finding from compare/playbook enrich.

**Acceptance:**

- [ ] Guard prompt includes dimension label + optional playbook guidance

---

### Task P2-6.4 — Artifact ops + report

**Files:** `review_agent/schemas/review_artifact.py`, `services/review_artifact.py`, `reports/generator.py`

New ops counters:

```python
quote_repair_attempts: int = 0
quote_repair_success: int = 0
guard_inference_ok: int = 0
guard_repair_attempts: int = 0
guard_repair_success: int = 0
```

Report markdown: separate **"failed grounding"** vs **"failed rationale guard"** vs **"inference-only (OK)"**.

**Acceptance:**

- [ ] Beta assessment artifact shows non-zero `guard_inference_ok` on liability fixture

---

### Task P2-6.5 — Tests

**File:** `review_agent/tests/test_guard_pass_tiered.py` (new, ~90 lines)

| Test | Expected |
|------|----------|
| `test_guard_inference_ok_keeps_non_compliant` | Mock INFERENCE_OK |
| `test_guard_unsupported_triggers_repair` | Mock repair + second guard FULL |
| `test_guard_unsupported_downgrades_after_repair_fail` | INCONCLUSIVE |
| `test_guard_does_not_clear_grounded_on_inference_ok` | `grounded` stays True |

Update `test_guard_pass.py` for schema migration (`supported` → `support_level`).

---

## 7. Config summary (Youngser)

| Env var | Default | Purpose |
|---------|---------|---------|
| `QUOTE_REPAIR_ENABLED` | `true` | P2-7 LLM quote repair before MCP |
| `QUOTE_REPAIR_MAX_CHARS` | `8000` | Section text budget for repair |
| `GROUNDING_DOWNGRADE_MODE` | `inconclusive` | or `keep_status_flag` |
| `GUARD_RATIONALE_REPAIR_ENABLED` | `true` | P2-6 one-shot rationale repair |
| `GUARD_PASS_MAX_TOKENS` | `512` | Guard/repair model budget |

---

## 8. Verification matrix (Youngser sign-off)

```powershell
cd "d:\Ankit_legal\Legal\review\review_agent"
python -m pytest tests/test_quote_repair_llm.py tests/test_guard_pass_tiered.py tests/test_guard_pass.py -v

cd "d:\Ankit_legal\Legal\temp_java_sync"
python beta_test\run_assessment.py
python run_full_e2e.py
```

| Gate | Before P2 | Target after P2 |
|------|-----------|-----------------|
| **G1** | §3 Liability `INCONCLUSIVE` after guard | **NON_COMPLIANT** (or COMPLIANT with inference flag) |
| **G2** | `guard_failed >= 1` on NDA | **0** on well-grounded liability finding |
| **G3** | `ungrounded_count >= 1` | **0** when repair succeeds |
| **G4** | `quote_repair_success >= 1` | logged in artifact ops |
| **G5** | `guard_inference_ok >= 1` | evaluative rationale kept |
| **G6** | Legal accuracy §3 | **NON_COMPLIANT** expected |
| **G7** | No false keep on hallucination | fabricated rationale still downgrades |

---

## 9. File touch list

| File | Task | Est. lines |
|------|------|------------|
| `services/quote_repair_llm.py` | P2-7.1 | +90 |
| `schemas/quote_repair.py` | P2-7.1 | +20 |
| `prompts/quote_repair.md` | P2-7.1 | +45 |
| `graph/nodes.py` | P2-7.2 | +40 |
| `config.py` | P2-7.2, P2-6 | +8 |
| `schemas/guard_llm.py` | P2-6.1 | +25 |
| `prompts/rationale_guard.md` | P2-6.1 | rewrite |
| `services/guard_pass.py` | P2-6.1–6.3 | +50 |
| `services/rationale_repair_llm.py` | P2-6.2 | +70 |
| `prompts/rationale_repair.md` | P2-6.2 | +40 |
| `schemas/review_artifact.py` | P2-6.4 | +10 |
| `services/review_artifact.py` | P2-6.4 | +15 |
| `reports/generator.py` | P2-6.4 | +10 |
| `tests/test_quote_repair_llm.py` | P2-7.4 | +80 |
| `tests/test_guard_pass_tiered.py` | P2-6.5 | +90 |
| `tests/test_guard_pass.py` | P2-6.5 | ~20 modify |

**Total:** ~280 prod + ~200 test

---

## 10. Out of scope (P3+)

| Item | Reason |
|------|--------|
| NLI entailment model (DeBERTa) | Separate Phase 15+; not sprint scope |
| YAML/regex rule engine | Explicitly excluded per P4/P6 |
| Fuzzy/Levenshtein quote match | Deterministic; user rejected as primary approach |
| Dev UI findings display fix | P1-7 frontend |
| Re-compare on guard fail | Too expensive; repair is cheaper |

---

## 11. Risk notes

| Risk | Mitigation |
|------|------------|
| Repair LLM still paraphrases | MCP verify fails closed; empty quote → downgrade |
| Latency +2 LLM calls/finding | Only on guard fail or grounding fail; cap concurrency |
| `INFERENCE_OK` too permissive | Golden tests on liability fixture; UNSUPPORTED on fabricated facts |
| Prompt injection via contract text | Repair/guard see section text only; structured output schema |

---

## 12. Definition of done (Youngser)

- [x] P2-6 and P2-7 implemented with tiered LLM + repair passes
- [x] No new deterministic rule engine files
- [x] MCP `verify_quote` remains final verbatim gate
- [ ] NDA beta: §3 liability stays **NON_COMPLIANT** with `guard_inference_ok` or `FULL`
- [ ] `ungrounded_count: 0` when repair succeeds on paraphrased quotes
- [ ] PR prefix: `Youngser P2: <description>`

---

## 13. Youngser execution checklist

1. **Youngser solution:** cite root cause from §1 in PR  
2. Implement P2-7 before P2-6 (grounding feeds guard)  
3. Mock-LLM unit tests for every tier path  
4. Beta run: attach `guard_failed`, `ungrounded_count`, `quote_repair_success`  
5. Lawyer review: one finding must show evaluative rationale **without** downgrade  

---

*End of Phase 19 P2 plan — Youngser*
