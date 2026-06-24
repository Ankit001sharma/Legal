# Phase 31 — Observability

**Status:** COMPLETE  
**Plan ID:** `DR-PHASE-31-OBSERVABILITY`  
**Priority:** P2  
**Scope:** Python only — `review_agent/`  
**Estimated diff:** ~120 LOC across 7 files (+ optional dep)  
**Depends on:** Phase 29 (errors/breakers), Phase 30 (stable `run_review`)  
**Non-goals:** ELK/Fluent Bit, Grafana dashboards, Java, frontend, OpenTelemetry (defer T4), `legal_ai_platform` `/metrics` wiring

---

## 1. Goal

Answer in production:

- **Why was this review slow?** → `compliance_stats.node_timings_ms` + `review_wall_ms` in artifact
- **Which stage failed?** → existing `failed_sections`, `degraded_sections`, breaker logs
- **MCP/LLM health?** → optional Prometheus counters; breaker state already logged

**Principle:** reuse `compliance_stats` → `ReviewArtifact` (already exported). Add Prometheus only as an optional mirror.

---

## 2. What already exists (do not rebuild)

| Asset | Location | Use |
|-------|----------|-----|
| `thread_id` / `run_id` | `review_graph.py:114`, `review_artifact.py:221` | Correlation ID (no new `review_id`) |
| `compliance_stats` | merged in every major node | Sink for timings + ops |
| `ReviewArtifact.ops` | `review_artifact.py:123-173` | Per-run counters (retries, guard, reranker) |
| `get_llm_limiter_stats()` | `llm_gateway.py:46-50` | `rate_limit_events` — wire once in `report_node` |
| Circuit breaker logs | `circuit_breaker.py:55-89` | MCP/LLM open/close |
| `build_runtime_settings_snapshot` | `config.py:167` | Reproducibility in artifact |
| Phase 29 `failed_sections` | `review_state.py:54` | Degraded quality trail |

**Gap:** logs lack correlation fields; no per-node wall times; no scrapeable metrics; `rate_limit_events` not in artifact stats.

---

## 3. Verified root causes

| # | Issue | Current code | Symptom |
|---|--------|--------------|---------|
| R1 | Plain `logging.getLogger` | ~15 modules | Cannot filter by `tenant_id` in ELK |
| R2 | No request context | `run_review` only sets `thread_id` in state | Logs not tied to review |
| R3 | No scrape metrics | no `prometheus_client` | No SLO dashboards |
| R4 | No node timing | 13-node linear graph | Manual grep for slow stage |

**Graph nodes (13)** — `review_graph.py:43-64`:

`load_memory` → `contract_parser` → `clause_detection` → `contract_routing` → `policy_discovery` → `index_policies` → `section_policy_retrieval` → `section_compare_llm` → `merge_section_findings` → `final_gap_verify` → `grounding` → `report` → `save_memory`

---

## 4. Task map (minimal, ordered)

| # | Task | Files | LOC | Default off? |
|---|------|-------|-----|--------------|
| **T1** | Contextvars + JSON log formatter (stdlib) | `observability/context.py`, `observability/logging.py` (NEW) | ~55 | JSON off |
| **T2** | Bind context + wall time in `run_review` | `review_graph.py` | ~20 | — |
| **T3** | Single node timer wrapper (all 13 nodes) | `observability/timing.py` (NEW), `review_graph.py` | ~35 | — |
| **T4** | Prometheus stubs + 3 chokepoints | `observability/metrics.py` (NEW), `document_client.py`, `llm_gateway.py` | ~45 | metrics off |
| **T5** | Wire `llm_rate_limit_events` to artifact | `graph/nodes.py` `report_node` | ~2 | — |
| **T6** | Settings + tests | `config.py`, `tests/test_observability.py` (NEW) | ~50 | — |

**Skip:** structlog (new required dep), OpenTelemetry, editing 15 log call sites (context filter covers all logs), platform `/metrics` route.

**Ship:** T1 → T2 → T3 → T5 → T6 → T4 (optional extra).

---

## 5. T1 — Stdlib structured logging (no structlog)

### New: `review_agent/observability/context.py`

```python
from contextvars import ContextVar

_tenant_id: ContextVar[str] = ContextVar("tenant_id", default="")
_thread_id: ContextVar[str] = ContextVar("thread_id", default="")
_node: ContextVar[str] = ContextVar("node", default="")

def bind_review_context(*, tenant_id: str, thread_id: str) -> None:
    _tenant_id.set(tenant_id)
    _thread_id.set(thread_id)

def set_current_node(name: str) -> None:
    _node.set(name)

def clear_review_context() -> None:
    _tenant_id.set("")
    _thread_id.set("")
    _node.set("")

def context_dict() -> dict[str, str]:
    return {
        "tenant_id": _tenant_id.get(),
        "thread_id": _thread_id.get(),
        "node": _node.get(),
    }
```

### New: `review_agent/observability/logging.py`

```python
class ReviewContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in context_dict().items():
            setattr(record, key, value)
        return True

def configure_review_logging(*, json_logs: bool = False) -> None:
    root = logging.getLogger("review_agent")
    if getattr(root, "_review_obs_configured", False):
        return
    root.addFilter(ReviewContextFilter())
    if json_logs:
        # JsonFormatter: emit one JSON object per line with msg, level, tenant_id, thread_id, node
        ...
    root._review_obs_configured = True  # type: ignore[attr-defined]
```

### Config (`config.py`)

```python
review_log_json: bool = False
review_metrics_enabled: bool = False
```

### Env

```env
REVIEW_LOG_JSON=false
REVIEW_METRICS_ENABLED=false
```

**Do not** call `configure_review_logging` at import time — call once at start of `run_review` only.

---

## 6. T2 — Bind context in `run_review`

### Change (`review_graph.py`)

```python
from review_agent.observability.context import bind_review_context, clear_review_context
from review_agent.observability.logging import configure_review_logging

async def run_review(...) -> ReviewState:
    get_settings.cache_clear()
    settings = get_settings()
    configure_review_logging(json_logs=settings.review_log_json)

    session_id = thread_id or str(uuid.uuid4())
    bind_review_context(tenant_id=tenant_id, thread_id=session_id)

    wall_start = time.perf_counter()
    try:
        ...
        result = await graph.ainvoke(initial, config=config)
        stats = dict(result.get("compliance_stats") or {})
        stats["review_wall_ms"] = round((time.perf_counter() - wall_start) * 1000, 2)
        result["compliance_stats"] = stats
        return result
    finally:
        clear_review_context()
```

**Use `thread_id` as correlation ID** — do not add a separate `review_id` (artifact already uses `run_id=thread_id`).

### One boundary log (optional, 4 lines total)

```python
logger.info("review_started tenant_id=%s thread_id=%s", tenant_id, session_id)
# ... on success in try before return:
logger.info("review_completed wall_ms=%s", stats["review_wall_ms"])
```

---

## 7. T3 — Node timing (one wrapper, not 13 file edits)

### New: `review_agent/observability/timing.py`

```python
def wrap_node(node_name: str, fn):
    async def wrapped(state, *args, **kwargs):
        set_current_node(node_name)
        start = time.perf_counter()
        try:
            out = await fn(state, *args, **kwargs)
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            record_node_duration(node_name, elapsed_ms)  # no-op if metrics off
        return _merge_node_timing(state, out or {}, node_name, elapsed_ms)
    return wrapped

def _merge_node_timing(state, out: dict, node_name: str, elapsed_ms: float) -> dict:
    stats = dict(state.get("compliance_stats") or {})
    stats.update(out.get("compliance_stats") or {})
    timings = dict(stats.get("node_timings_ms") or {})
    timings[node_name] = elapsed_ms
    stats["node_timings_ms"] = timings
    out["compliance_stats"] = stats
    return out
```

### Wire in `build_review_graph` only

```python
from review_agent.observability.timing import wrap_node

def _add(graph, name, fn, **partial_kwargs):
    graph.add_node(name, wrap_node(name, partial(fn, **partial_kwargs)))

_add(graph, "load_memory", load_memory_node, memory_client=memory_client)
_add(graph, "contract_parser", contract_parser_node, client=client)
# ... all 13 nodes
```

**Result:** `compliance_stats.node_timings_ms` lands in artifact via existing `compliance_stats` field — **no schema change**.

---

## 8. T4 — Prometheus (optional, default off)

### Dependency (`pyproject.toml`)

```toml
[project.optional-dependencies]
observability = ["prometheus-client>=0.20.0"]
```

### New: `review_agent/observability/metrics.py`

```python
_ENABLED = False

def configure_metrics(enabled: bool) -> None:
    global _ENABLED
    _ENABLED = enabled

def record_review_duration(seconds: float) -> None: ...
def record_node_duration(node: str, seconds: float) -> None: ...
def record_mcp_request(path: str, status: str) -> None: ...
def record_llm_call(operation: str, status: str) -> None: ...
```

When `_ENABLED` is false or `prometheus_client` not installed → **no-op** (zero overhead).

### Instrument exactly 3 sites

| Site | File | When |
|------|------|------|
| Review wall time | `review_graph.py` `run_review` | after `ainvoke` |
| MCP request | `document_client.py` `_request` | success: `status=str(response.status_code)`; failure: `status="error"` |
| LLM structured call | `llm_gateway.py` `invoke_structured` | success / `LLMUnavailableError` / other |

### Metrics names (for Grafana later)

```
review_duration_seconds          Histogram
review_node_duration_seconds     Histogram  label: node
review_mcp_requests_total        Counter    labels: path, status
review_llm_calls_total           Counter    labels: operation, status
```

### Exposure (out of scope for this PR)

- **Do not** add FastAPI `/metrics` in `review_agent` (no app today).
- Document: platform mounts `generate_latest()` or ops runs sidecar later.
- Optional dev: `REVIEW_METRICS_PORT=9091` → `start_http_server` in `run_review` **only if** explicitly set (follow-up; not required for DoD).

---

## 9. T5 — Wire existing LLM limiter stats

### Change (`graph/nodes.py` `report_node` ~L369)

```python
from review_agent.models.llm_gateway import get_llm_limiter_stats

stats = dict(state.get("compliance_stats") or {})
stats["llm_rate_limit_events"] = get_llm_limiter_stats()["rate_limit_events"]
```

Already flows to `ReviewReport.metadata.compliance_stats` and artifact.

---

## 10. T6 — Tests

### `tests/test_observability.py`

```python
def test_context_filter_injects_fields():
    bind_review_context(tenant_id="t1", thread_id="run-1")
    set_current_node("policy_discovery")
    record = logging.LogRecord(...)
    ReviewContextFilter().filter(record)
    assert record.tenant_id == "t1"
    assert record.node == "policy_discovery"

def test_merge_node_timing_accumulates():
    state = {"compliance_stats": {"node_timings_ms": {"load_memory": 1.0}}}
    out = _merge_node_timing(state, {}, "contract_parser", 2.5)
    assert out["compliance_stats"]["node_timings_ms"] == {
        "load_memory": 1.0, "contract_parser": 2.5
    }

def test_metrics_noop_when_disabled():
    configure_metrics(False)
    record_mcp_request("/tools/search_policy", "200")  # must not raise

@pytest.mark.asyncio
async def test_run_review_sets_wall_ms(monkeypatch):
    # mock graph.ainvoke → minimal state; assert review_wall_ms in result
```

Use existing `test_review_e2e` patterns with heavy mocks — no Postgres required.

---

## 11. Files touched

| File | T1 | T2 | T3 | T4 | T5 | T6 |
|------|----|----|----|----|----|-----|
| `observability/context.py` | ✓ | | | | | ✓ |
| `observability/logging.py` | ✓ | | | | | ✓ |
| `observability/timing.py` | | | ✓ | | | ✓ |
| `observability/metrics.py` | | | | ✓ | | ✓ |
| `observability/__init__.py` | ✓ | | | | | |
| `config.py` | ✓ | | | | | |
| `graph/review_graph.py` | | ✓ | ✓ | ✓ | | ✓ |
| `clients/document_client.py` | | | | ✓ | | |
| `models/llm_gateway.py` | | | | ✓ | | |
| `graph/nodes.py` | | | | | ✓ | |

**Not touched:** `document_core`, research agent, Java, frontend, 13 individual node files.

---

## 12. Definition of done

- [x] `REVIEW_LOG_JSON=true` → log lines include `tenant_id`, `thread_id`, `node` (stdlib JSON)
- [x] `compliance_stats.review_wall_ms` + `node_timings_ms` present after `run_review`
- [x] `compliance_stats.llm_rate_limit_events` in report/artifact
- [x] `REVIEW_METRICS_ENABLED=true` + `pip install -e ".[observability]"` increments Prometheus counters (unit test with `REGISTRY` collect)
- [x] `REVIEW_LOG_JSON=false` and `REVIEW_METRICS_ENABLED=false` → no measurable overhead vs baseline (same code paths, no-op stubs)
- [x] `pytest tests/test_observability.py -q --noconftest` passes

---

## 13. Implementation order

```
T1 context/logging → T3 timing wrapper + T2 run_review → T5 report_node → T6 tests → T4 metrics (optional install)
```

Single PR for T1–T3–T5–T6. T4 can be same PR if `observability` extra stays optional.

---

## 14. Out of scope / deferred

| Item | Where |
|------|--------|
| OpenTelemetry spans | Follow-up PR (`OTEL_ENABLED`) — redundant with `node_timings_ms` for v1 |
| structlog | Avoid new required dep; stdlib sufficient |
| Rewrite 200 log lines | Context filter auto-enriches all `review_agent.*` logs |
| Platform `/metrics` route | `legal_ai_platform` when review API hosted |
| Log shipping (Fluent Bit) | Ops |
| Grafana dashboards | Metric names documented in §8 |
| Token cost accounting | Future |

---

## 15. Risk register

| Risk | Mitigation |
|------|------------|
| `compliance_stats` overwrite drops timings | `_merge_node_timing` spreads prior `state` + `out` stats |
| Double `configure_review_logging` in tests | Guard with `_review_obs_configured` flag |
| Prometheus import failure | Try/except in `metrics.py`; default no-op |
| Wrapper breaks `partial(...)` signature | Wrap **after** `partial(fn, client=client)` |

---

## 16. Example artifact snippet (post-P31)

```json
{
  "compliance_stats": {
    "review_wall_ms": 8420.5,
    "node_timings_ms": {
      "policy_discovery": 1200.0,
      "section_policy_retrieval": 3100.0,
      "section_compare_llm": 2800.0,
      "grounding": 450.0
    },
    "llm_rate_limit_events": 0,
    "runtime_settings": { "...": "..." }
  }
}
```

This answers “why slow” without a separate APM product.
