# Phase 21 P0 — LLM 429 Backoff + Global Concurrency

**Plan ID:** `DR-PHASE-21-P0-LLM-LIMIT`  
**Priority:** P0  
**Impact:** Prevents 429 cascades — **indirect LLM savings** (failed compares no longer burn gap/guard/final-verify)  
**Accuracy:** ★★★★★ (same prompts/schemas; more compares complete successfully)  
**Status:** **IMPLEMENTED** — verification items below remain  
**Scope:** `review_agent` only — **one file owns behavior** (`models/llm_gateway.py`) + thin config + tests  
**Goal:** Stop 429 cascades on large reviews **without** reducing compare/guard accuracy or removing quality gates  
**Non-goals:** Batching guard, skipping final-verify, classifier redesign (Phase 21 P1+)

---

## 0. Problem (root cause)

| Symptom | Cause |
|---------|--------|
| `INSUFFICIENT_POLICY_CONTEXT` + `Rate limit exceeded` / code `1300` | Mistral 429 mid-review |
| 8+ sections never compared | Parallel batches (compare×3 + classify×3 + guard×4) exceed provider RPM |
| Same run: §3 correct, §5/8 missed | Failed compares **not retried**; downstream marks gap/unclear and **burns more LLM** |

**Today:** Every service calls `invoke_structured()` with **no shared limit** and **no 429 retry**.  
`compliance_llm_max_retries` exists in config but is **only used in `contract_routing.py`**, not the main pipeline.

---

## 1. Design principle (accuracy-safe)

1. **Single choke point** — all review LLM calls already go through `invoke_structured()` (9 call sites). Add limit + retry **there only**.
2. **Retry only rate limits** — never retry JSON parse / validation / empty response (avoids duplicate/conflicting findings).
3. **Same prompts, same schemas** — no truncation or model change in P0.
4. **Fail loud after exhaustion** — callers keep existing failure paths; error message includes `rate_limited` so ops/report can distinguish from “no policy”.
5. **Global concurrency < provider RPM** — default **2** in-flight LLM requests for entire review graph.

---

## 2. Target behavior

```text
invoke_structured()
  ├─ acquire global LLM semaphore (max 2)
  ├─ try structured.ainvoke
  │    └─ on 429/rate_limit → backoff 2s → 4s → 8s (max 3 retries)
  ├─ on other errors → existing JSON fallback (unchanged)
  └─ release semaphore
```

**Effective call shape (11 sections, your Cisco run):**

| Stage | Before (peak parallel LLM) | After P0 |
|-------|---------------------------|----------|
| Classify batches | up to 3 | max 2 (serialized with compare) |
| Compare batches | up to 3 | max 2 |
| Guard (after compare) | up to 4 | max 2 |
| **Peak in-flight** | **7–10** | **2** |

Wall-clock may increase ~20–40%; **accuracy improves** because compares complete instead of 429-failing.

---

## 3. Implementation (minimal diff)

### 3.1 Config — `review_agent/config.py`

Add (env-overridable):

```python
llm_global_concurrency: int = 2          # REVIEW_LLM_GLOBAL_CONCURRENCY
llm_rate_limit_max_retries: int = 3      # REVIEW_LLM_RATE_LIMIT_MAX_RETRIES
llm_rate_limit_backoff_base_seconds: float = 2.0
llm_rate_limit_backoff_max_seconds: float = 30.0
```

**Do not wire `compliance_llm_max_retries` into gateway** — leave routing loop as-is; avoid two retry systems.

**Align defaults (no new code in callers):**

| Setting | Old default | P0 default | Why |
|---------|-------------|------------|-----|
| `section_compare_concurrency` | 3 | **2** | Match global cap |
| `guard_pass_concurrency` | 4 | **2** | Match global cap |

Global semaphore is authoritative; lowering these avoids pointless task queue depth.

---

### 3.2 Core — `review_agent/models/llm_gateway.py`

**Add (~80 lines, no new dependencies):**

```python
# Module-level lazy init (test-reset via reset_llm_limiter())
_limiter: _ReviewLLMLimiter | None = None

def _is_rate_limit_error(exc: BaseException) -> bool:
    """Match Mistral 1300, OpenAI 429, generic 'rate limit' strings."""
    ...

async def invoke_structured(...):
    limiter = get_llm_limiter()
    async with limiter.semaphore:
        return await _invoke_structured_with_rate_limit_retry(...)
```

**`_is_rate_limit_error` checks (order):**

1. `httpx.HTTPStatusError` → `response.status_code == 429`
2. Exception string contains (case-insensitive): `rate limit`, `rate_limited`, `"code":"1300"`, `429`
3. LangChain wrapper: walk `__cause__` chain (max depth 4)

**Retry loop (rate limit only):**

```python
for attempt in range(max_retries + 1):
    try:
        return await _invoke_once(...)
    except Exception as exc:
        if not _is_rate_limit_error(exc) or attempt == max_retries:
            raise
        delay = min(base * (2 ** attempt), max_backoff) + random.uniform(0, 0.5)
        logger.warning("LLM rate limited (attempt %s/%s), sleeping %.1fs", ...)
        await asyncio.sleep(delay)
```

**`_invoke_once`:** move current `invoke_structured` body (structured output → JSON fallback). **No change** to fallback logic.

**Optional accuracy hook (5 lines):** increment `limiter.rate_limit_events` counter; expose via `get_llm_limiter_stats()` for artifact ops later (P1). Not required for P0 ship.

---

### 3.3 Test reset — `tests/conftest.py` or test helper

```python
def reset_llm_limiter():
    llm_gateway.reset_llm_limiter()
```

Call in tests that patch `invoke_structured` to avoid cross-test semaphore leak.

---

### 3.4 Tests — `tests/test_llm_gateway_rate_limit.py` (new, ~120 lines)

| Test | Assert |
|------|--------|
| `test_is_rate_limit_mistral_1300` | Message with `rate_limited` + `1300` → True |
| `test_is_rate_limit_not_validation` | `ValidationError` → False |
| `test_retry_succeeds_second_attempt` | Mock `ainvoke` fails 429 once, succeeds once → 2 calls, 1 sleep |
| `test_retry_exhausted_raises` | 4×429 → raises; message preserved |
| `test_global_semaphore_serializes` | 3 concurrent `invoke_structured` with slow mock → max 2 overlap |
| `test_non_rate_limit_no_retry` | JSON error → 1 call, no sleep |

Use `monkeypatch` + `asyncio.sleep` mock for speed.

---

### 3.5 `.env.example` — document vars

```env
REVIEW_LLM_GLOBAL_CONCURRENCY=2
REVIEW_LLM_RATE_LIMIT_MAX_RETRIES=3
```

---

## 4. What NOT to change (accuracy)

| Do not | Reason |
|--------|--------|
| Disable guard / final-verify / quote repair | Quality gates stay |
| Retry on `ValidationError` / bad JSON | Could double-emit findings |
| Lower `section_compare_max_tokens` | Truncation loses policy context |
| Replace LLM classifier with lexical-only | P1 task |
| Catch 429 in `compare_section_batch` and return `_failure_items` silently | Must retry at gateway first |

**Caller behavior unchanged:** `section_compare_llm.py` still returns `_failure_items` only **after** gateway exhausts retries — but retries should recover most 429s.

---

## 5. Remove / dedupe (minimal cleanup)

| Item | Action |
|------|--------|
| `compliance_llm_max_retries` in config | **Keep** — used by routing; add comment “routing only; pipeline uses llm_gateway retries” |
| Duplicate retry logic in `contract_routing.py` | **Optional P0.1:** delegate routing LLM to same `_invoke_once` helper — **skip for P0** to minimize diff |
| Unused imports in touched files | Fix if linter flags |

No file deletions in P0.

---

## 6. Rollout & verification

### 6.1 Local (your Cisco Dev UI case)

1. Set `REVIEW_LLM_GLOBAL_CONCURRENCY=2` in `review_agent/.env`
2. Re-run same 11-section × 17-policy paste review
3. **Pass criteria:**
   - Zero `Rate limit exceeded` in findings rationale
   - §5 minerals + §8 MSS get compare results (NON_COMPLIANT or INCONCLUSIVE with real quotes, not 429 text)
   - `playbook_compare_count` ≥ 8 (was 5)
   - Wall-clock < 6 min acceptable

### 6.2 Automated

```powershell
cd Legal\review\review_agent
python -m pytest tests/test_llm_gateway_rate_limit.py tests/test_section_compare.py -v
python -m pytest tests/ -q --ignore=tests/test_review_e2e.py  # if no MCP keys
```

### 6.3 Regression harness

```powershell
cd Legal\temp_java_sync
python beta_test/run_cisco_assessment.py
```

Expect: still **6/6** legal accuracy; elapsed may be +30–60s.

---

## 7. File checklist

| File | Change | Lines (est.) |
|------|--------|-------------|
| `models/llm_gateway.py` | Semaphore + 429 detect + retry | +85 |
| `config.py` | 4 new settings; default concurrency 2 | +12 |
| `tests/test_llm_gateway_rate_limit.py` | New | +120 |
| `.env.example` | Document vars | +4 |
| `review_agent/.env` | User sets concurrency (not committed) | — |

**Total production code: ~100 lines.**

---

## 8. Risk matrix

| Risk | Mitigation |
|------|------------|
| Slower reviews | Acceptable; complete > fast+empty |
| Retry doubles cost on sustained 429 | Max 3 retries then fail; log warning |
| Semaphore deadlock | `async with` only; no nested acquire |
| Tests flaky on timing | Mock `asyncio.sleep` |

---

## 9. Done definition (P0)

- [x] All LLM calls pass through rate-limit-aware `invoke_structured`
- [x] Default global concurrency = 2
- [x] 429 retries up to 3 with exponential backoff
- [x] Non-429 errors: no extra retries (no JSON-parse retry loop)
- [x] Rate-limit errors do **not** fall through to JSON fallback in `_invoke_once`
- [x] `reset_llm_limiter()` + `get_llm_limiter_stats()` for tests/ops
- [x] Unit tests green (`tests/test_llm_gateway_rate_limit.py`)
- [x] Config: `llm_global_concurrency`, `llm_rate_limit_max_retries`, backoff settings
- [x] `.env.example` documented
- [ ] Cisco Dev UI re-run: no 429 strings in findings / rationales
- [ ] `run_cisco_assessment.py` still ≥ 6/6 with `rate_limit_events` logged if any
- [ ] Optional: wire `get_llm_limiter_stats()` → `ReviewArtifact.ops.rate_limit_events` (+5 lines)

---

## 9b. Implemented surface (reference)

| File | What |
|------|------|
| `models/llm_gateway.py` | Semaphore, `_is_rate_limit_error`, exponential backoff, `invoke_structured` choke point |
| `config.py` | `llm_global_concurrency=2`, retry/backoff env vars |
| `config.py` | `section_compare_concurrency=2`, `guard_pass_concurrency=2` aligned with global cap |
| `tests/test_llm_gateway_rate_limit.py` | 7 tests — 429 detect, retry, no-retry on validation, semaphore |

**Env (production):**

```env
LLM_GLOBAL_CONCURRENCY=2
LLM_RATE_LIMIT_MAX_RETRIES=3
LLM_RATE_LIMIT_BACKOFF_BASE_SECONDS=2.0
LLM_RATE_LIMIT_BACKOFF_MAX_SECONDS=30.0
```

**Do not duplicate retry:** `compliance_llm_max_retries` stays **routing-only** (`contract_routing.py`) — intentionally not wired into gateway.

---

## 9c. Remove / do not add (cleanup)

| Item | Action |
|------|--------|
| Second retry system in compare/guard | **Do not add** — gateway is single choke point |
| Retry on JSON parse / ValidationError | **Forbidden** — causes duplicate/conflicting findings |
| Per-service semaphores replacing global | **Avoid** — global cap is authoritative |
| Lower concurrency below 2 on Mistral free tier | User env only if 429 persists after P0-B/C/P1 guard |

---

## 10. PR title

`Youngser P0: LLM global concurrency + 429 backoff in llm_gateway`

---

*Parent backlog: Phase 21 — LLM cost + accuracy. **P0 done.** Next: P1 dedupe/cap (`PHASE21_P1_DEDUPE_DIMENSIONS_PLAN.md`).*
