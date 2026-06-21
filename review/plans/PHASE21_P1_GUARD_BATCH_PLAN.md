# Phase 21 P1 — Batch Guard / Guard Only NON_COMPLIANT

**Plan ID:** `DR-PHASE-21-P1-GUARD-BATCH`  
**Priority:** P1  
**Impact:** **−8 to 12 LLM calls** per large review  
**Accuracy:** ★★★ (violations still guarded; COMPLIANT rationales trusted post-grounding)  
**Status:** Implemented  
**Depends on:** P0-A rate limit, P0-B unclear cap, P0-C lexical ESG  

---

## Implementation checklist

- [x] **P1-G.1** `guard_pass_non_compliant_only=true` — skip COMPLIANT grounded findings
- [x] **P1-G.2** Batch guard (`guard_pass_batch_size=4`) — one LLM call per batch
- [x] **P1-G.3** Repair path unchanged — UNSUPPORTED → single re-guard after repair
- [x] **P1-G.4** Batch fallback — exception or omitted finding_id → per-finding guard
- [x] **P1-G.5** Stats: `guard_batch_calls`
- [x] **P1-G.6** Tests: batch, compliant skip, tiered repair regression
- [ ] **P1-G.7** Cisco / Dev UI re-run — confirm `guard_batch_calls` << `guard_checked`

---

## Config (`.env`)

```env
GUARD_PASS_BATCH_SIZE=4
GUARD_PASS_NON_COMPLIANT_ONLY=true
GUARD_PASS_CONCURRENCY=2
```

---

*Phase sequence: P0-C (done) → **P1 guard batch (done)** → next backlog items.*
