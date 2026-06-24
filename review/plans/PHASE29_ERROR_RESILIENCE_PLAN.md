# Phase 29 — Error Handling & Resilience

**Status:** COMPLETE  
**Plan ID:** `DR-PHASE-29-RESILIENCE`  
**Priority:** P1  
**Duration:** ~5–7 days  
**Depends on:** Phase 27 (unified MCP client)  
**Non-goals:** LLM rate limiting, Redis, dead-letter queue persistence, Java errors

---

## 1. Goal

**Fail fast** on unrecoverable errors; **degrade gracefully** on transient ones. Stop silent `except Exception` from masking MCP/LLM outages.

**No rate-limit work** — semaphore/backoff/Redis explicitly excluded.

---

## 2. Root causes

| # | Root cause | Impact |
|---|------------|--------|
| R1 | 23× `except Exception` → log warning, continue | Garbage review output on outage |
| R2 | No circuit breaker — 20 sections × 3 retries = 60 failed LLM calls | 2+ min wasted, wrong verdicts |
| R3 | MCP down mid-review — no fast abort | Timeouts per section |
| R4 | Failed sections not tracked | Cannot retry or report degraded quality |

---

## 3. What already exists

| Asset | Location |
|-------|----------|
| `ReviewState` | `review_state.py` |
| Preflight MCP check | `review_preflight.py` |
| Retry in MCP client | `document_client.py` (after P27) |
| LLM gateway | `llm_gateway.py` — keep retry logic, add breaker only |

---

## 4. Task map

| # | Task | Est. | Files | Risk |
|---|------|------|-------|------|
| **T1** | Error taxonomy | 4h | `review_agent/errors.py` (NEW) | Low |
| **T2** | Circuit breaker util | 4h | `review_agent/resilience/circuit_breaker.py` (NEW) | Low |
| **T3** | Wire MCP + LLM breakers | 1d | `document_client.py`, `llm_gateway.py` | Med |
| **T4** | Tighten top 8 `except Exception` sites | 1d | routing, classifier, compare, discovery | Med |
| **T5** | `failed_sections` in state | 4h | `review_state.py`, compare/classify nodes | Low |
| **T6** | Tests | 1d | `tests/test_circuit_breaker.py`, node tests | Low |

---

## 5. T1 — Error taxonomy (minimal)

```python
# review_agent/errors.py

class RecoverableError(Exception):
    """Retry or degrade."""

class FatalPipelineError(Exception):
    """Abort review with clear code."""

class MCPUnreachableError(FatalPipelineError): ...
class LLMUnavailableError(RecoverableError): ...
```

Map:
- `httpx.ConnectError` → `MCPUnreachableError`
- MCP 5xx → `RecoverableError`
- MCP 4xx (except 404) → `FatalPipelineError`
- LLM structured parse fail → `RecoverableError` (existing fallback path)

---

## 6. T2 — Circuit breaker (~40 LOC)

```python
class CircuitBreaker:
    def __init__(self, name: str, failure_threshold=5, reset_timeout=60.0): ...
    def allow(self) -> bool: ...
    def record_success(self): ...
    def record_failure(self): ...
```

Module-level: `_mcp_breaker`, `_llm_breaker` (per-process — acceptable for single-worker; multi-worker is P30 concern).

When open: raise `FatalPipelineError("circuit_open:mcp")` immediately.

---

## 7. T3 — Integration points

**MCP:** Wrap `_post` final failure → `record_failure`; success → `record_success`. If `not breaker.allow()` skip retries.

**LLM:** At start of `invoke_structured`, if LLM breaker open → raise `LLMUnavailableError` → nodes use lexical fallback where exists.

---

## 8. T4 — Priority exception tightening

| File | Current behavior | Target |
|------|------------------|--------|
| `contract_routing.py` L101,114,266 | Silent default topics | Re-raise `FatalPipelineError` on MCP down; keep fallback on parse fail |
| `section_classifier.py` L344+ | All → general | Distinguish MCP vs parse |
| `section_compare_llm.py` L329+ | INSUFFICIENT_POLICY_CONTEXT | Record in `failed_sections` |
| `discovery_nodes.py` | Empty discovery | Warn + `failed_sections` if MCP error |

**Do not** refactor all 23 sites — fix **top 8 by impact** only.

---

## 9. T5 — `failed_sections` state

```python
# review_state.py
failed_sections: Annotated[list[dict], operator.add]  # or dedupe by section_id

# entry shape:
{"section_id": "...", "stage": "classify|compare|retrieve", "error_code": "...", "message": "..."}
```

Surface in `review_artifact.py` output envelope as `degraded_sections`.

---

## 10. Definition of done

- [x] MCP stopped → review aborts in &lt;10s with clear error (not 20× section timeouts)
- [x] LLM breaker open → classifier uses lexical-only path
- [x] `failed_sections` populated in artifact when compare fails
- [x] No rate-limit / Redis changes

---

## 11. Out of scope

- LLM rate limit detection/backoff changes
- Redis distributed breaker
- Persistent dead-letter queue (DB table)
