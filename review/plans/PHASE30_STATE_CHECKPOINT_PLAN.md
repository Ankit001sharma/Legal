# Phase 30 — State & Checkpointing

**Status:** COMPLETE  
**Plan ID:** `DR-PHASE-30-STATE-CHECKPOINT`  
**Priority:** P1  
**Scope:** Python only — `review_agent/` (+ 1 optional dep)  
**Estimated diff:** ~80 LOC across 6 files  
**Depends on:** Phase 29 (clean failure paths)  
**Non-goals:** Java, frontend, Redis, platform `SessionService`, graph parallelism (P34)

---

## 1. Goal

Make the review runtime safe on a **long-lived Python process**:

1. Settings refresh without restart (for callers outside `run_review`)
2. Classify batch sizing does not leak across reviews
3. Checkpoint memory does not grow unbounded per review
4. Warning lists do not duplicate identical strings

**Not required for MVP:** durable resume/HITL (graph has no interrupts today).

---

## 2. Verified root causes (codebase audit 2026-06-24)

| # | Issue | File:line (current) | Actual symptom |
|---|--------|---------------------|----------------|
| R1 | `@lru_cache` on `get_settings()` | `config.py:201-205` | Env changes invisible until process restart (except `run_review`, which calls `cache_clear()` at `review_graph.py:101`) |
| R2 | Module global `_classify_parse_failures` | `section_classifier.py:61,381,545-546` | Review N+1 inherits shrunk batch size after review N had parse failures |
| R3 | Module singleton `MemorySaver()` | `review_graph.py:33,81` | Every `run_review` stores ~14 node checkpoints per `thread_id`; memory never freed |
| R4 | `warnings: operator.add` | `review_state.py:53` | Same warning string appended many times (classifier/retrieval nodes) |

### R2 precision (important)

`classify_all_sections` computes `batch_size` **once**, then runs batches **in parallel** via `gather_limited`. The global increment in `_classify_batch_llm` happens **after** batch_size is fixed, so it **never** shrinks batches within the same review — it only poisons the **next** review.

**Minimal fix:** delete the global and the dead `batch_size` penalty block. No `ReviewState` field required.

---

## 3. Task map (minimal, ordered)

| # | Task | Files | LOC | Risk |
|---|------|-------|-----|------|
| **T1** | TTL settings cache + `cache_clear()` compat | `config.py` | ~25 | Low |
| **T2** | Remove cross-review classify leak | `section_classifier.py` | ~-5 | Low |
| **T3a** | Drop module `MemorySaver` (default) | `review_graph.py` | ~5 | Low |
| **T3b** | Optional Postgres checkpointer | `checkpoint.py` (NEW), `review_graph.py`, `pyproject.toml` | ~40 | Med |
| **T4** | Dedupe warnings reducer | `review_state.py` | ~12 | Low |
| **T5** | Tests | `test_config.py`, `test_section_classifier.py`, `test_state_reducers.py` (NEW) | ~60 | Low |

**Ship T1 + T2 + T3a + T4 + T5 first.** T3b only when prod needs checkpoint persistence.

---

## 4. T1 — Settings TTL (`config.py`)

### Change

Replace `@lru_cache` with a 30s monotonic TTL. Preserve `get_settings.cache_clear()` for tests and `run_review`.

```python
import os
import time

_settings_cache: ReviewSettings | None = None
_settings_cached_at: float = 0.0
_SETTINGS_TTL = float(os.getenv("SETTINGS_CACHE_TTL_SECONDS", "30"))


def get_settings() -> ReviewSettings:
    global _settings_cache, _settings_cached_at
    now = time.monotonic()
    if _settings_cache is not None and (now - _settings_cached_at) < _SETTINGS_TTL:
        return _settings_cache
    settings = ReviewSettings()
    _maybe_warn_discovery_cap(settings)
    _settings_cache = settings
    _settings_cached_at = now
    return settings


def _clear_settings_cache() -> None:
    global _settings_cache, _settings_cached_at
    _settings_cache = None
    _settings_cached_at = 0.0


get_settings.cache_clear = _clear_settings_cache  # type: ignore[attr-defined]
```

### Do not change

- `run_review` line 101 `get_settings.cache_clear()` — keep it (forces fresh settings every review).
- `_config_cap_warned` global — out of scope (P34).

### Env

```env
SETTINGS_CACHE_TTL_SECONDS=30   # default
```

### Test (`test_config.py`)

```python
def test_settings_ttl_refreshes(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("SECTION_CLASSIFY_BATCH_SIZE", "2")
    assert get_settings().section_classify_batch_size == 2
    monkeypatch.setenv("SECTION_CLASSIFY_BATCH_SIZE", "5")
    assert get_settings().section_classify_batch_size == 2  # still cached
    get_settings.cache_clear()
    assert get_settings().section_classify_batch_size == 5
```

---

## 5. T2 — Classify parse-failure leak (`section_classifier.py`)

### Delete (3 items)

1. Line 61: `_classify_parse_failures = 0`
2. Lines 313, 381: `global` and `_classify_parse_failures += 1`
3. Lines 545-546: batch_size penalty block

### Why this is correct

- Parallel batches cannot react to mid-flight failures anyway.
- Removing the global fixes cross-review bleed with zero new state wiring.
- `_classify_batch_llm` single-section retry on salvage failure (lines 394-404) stays unchanged.

### Test (`test_section_classifier.py`)

```python
@pytest.mark.asyncio
async def test_classify_parse_failure_does_not_shrink_next_review(monkeypatch):
    section_classifier._classify_parse_failures = 99  # simulate poisoned global (pre-fix)
    # After fix: attribute removed; test instead:
    # 1) force salvage failure on first classify_all_sections call
    # 2) second call with batch_size=2 still uses 2-section batches (len(batches)==1 for 2 sections)
```

Post-fix test pattern:

```python
async def test_two_classify_runs_use_same_batch_size(monkeypatch):
    sections = [_section("A", "liability text", "1"), _section("B", "indemnity", "2")]
    batch_sizes: list[int] = []
    original = section_classifier.classify_sections_batch

    async def spy(batch, **kwargs):
        batch_sizes.append(len(batch))
        return await original(batch, **kwargs)

    monkeypatch.setattr(section_classifier, "classify_sections_batch", spy)
    # force parse failure path once via invoke_structured raising...
    await section_classifier.classify_all_sections(sections, settings=ReviewSettings())
    await section_classifier.classify_all_sections(sections, settings=ReviewSettings())
    assert batch_sizes[0] == batch_sizes[2]  # second review first batch same size as first review
```

---

## 6. T3a — Remove module MemorySaver (default, required)

### Facts

- Graph has **no** `interrupt`, **no** resume, **no** HITL nodes.
- `run_review` always passes a new `thread_id` (or explicit one) — checkpointing buys nothing today except memory use.
- `build_review_graph()` is called per `run_review` but shares one `_checkpointer` → leak.

### Change (`review_graph.py`)

```python
# DELETE:
# from langgraph.checkpoint.memory import MemorySaver
# _checkpointer = MemorySaver()

def build_review_graph(client, memory_client=None):
    ...
    return graph.compile()  # no checkpointer
```

Also remove unused `thread_id` from `config`? **No** — keep `config = {"configurable": {"thread_id": session_id}}` and `ReviewState.thread_id` for artifact/logging; harmless without checkpointer.

### Test

Existing `test_review_e2e.py` integration tests must still pass — they mock LLM, not checkpointer.

---

## 7. T3b — Optional Postgres checkpointer (prod only)

**Skip unless** you need crash-resume or audit trail of graph state.

### Dependency (`pyproject.toml`)

```toml
[project.optional-dependencies]
checkpoint = ["langgraph-checkpoint-postgres>=2.0.0"]
```

### New file: `review_agent/checkpoint.py` (~35 LOC)

```python
"""Optional LangGraph checkpointer — off by default."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver


@asynccontextmanager
async def review_checkpointer() -> AsyncIterator[BaseCheckpointSaver | None]:
    """Yield None (no checkpoint) unless REVIEW_CHECKPOINT_ENABLED=true."""
    if os.getenv("REVIEW_CHECKPOINT_ENABLED", "").lower() not in ("1", "true", "yes"):
        yield None
        return

    url = os.getenv("REVIEW_CHECKPOINT_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("REVIEW_CHECKPOINT_ENABLED requires REVIEW_CHECKPOINT_DATABASE_URL or DATABASE_URL")

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(url) as saver:
        await saver.setup()  # idempotent; creates checkpoint_* tables
        yield saver
```

### Wire in `run_review` (not module import time)

```python
async def run_review(...) -> ReviewState:
    ...
    async with review_checkpointer() as checkpointer:
        graph = build_review_graph(client, memory_client=memory_client)
        compiled = graph.compile(checkpointer=checkpointer) if checkpointer else graph
        # build_review_graph returns compiled graph today — refactor:
        #   build_review_graph returns StateGraph (uncompiled)
        #   run_review calls .compile(checkpointer=...)
        return await compiled.ainvoke(initial, config=config)
```

### Refactor note (minimal)

Split `build_review_graph` into:

- `build_review_graph(...)` → returns `StateGraph` (no `.compile()`)
- `run_review` → `graph.compile(checkpointer=cp)` or `graph.compile()`

**One call site** (`run_review`) — no platform changes.

### Env

```env
REVIEW_CHECKPOINT_ENABLED=false          # default — matches T3a behavior
REVIEW_CHECKPOINT_DATABASE_URL=          # optional; falls back to DATABASE_URL
```

### Ops

- Reuse existing Postgres (same instance as pgvector is fine; separate tables `checkpoints`, `checkpoint_writes`, …).
- Call `setup()` once per process startup (handled inside `review_checkpointer()` context).
- **Do not** store checkpointer on a module global without lifecycle — use `async with` per `run_review` OR platform lifespan if later hosted in FastAPI.

---

## 8. T4 — Dedupe warnings (`review_state.py`)

### Change

```python
def _merge_warnings(existing: list[str], new: list[str]) -> list[str]:
    if not new:
        return existing
    seen = set(existing)
    merged = list(existing)
    for item in new:
        if item not in seen:
            seen.add(item)
            merged.append(item)
    return merged


class ReviewState(TypedDict, total=False):
    ...
    warnings: Annotated[list[str], _merge_warnings]
    failed_sections: Annotated[list[dict[str, Any]], operator.add]  # unchanged
```

### Do not dedupe

- `failed_sections` — entries differ by `section_id`/`stage`; keep `operator.add`.

### Test (`tests/test_state_reducers.py`)

```python
from review_agent.state.review_state import _merge_warnings

def test_merge_warnings_dedupes():
    assert _merge_warnings(["a"], ["a", "b"]) == ["a", "b"]
    assert _merge_warnings(["a", "b"], ["b", "c"]) == ["a", "b", "c"]
```

Export `_merge_warnings` only for tests, or test via a tiny public `merge_warnings` alias.

---

## 9. Files touched (checklist)

| File | T1 | T2 | T3a | T3b | T4 | T5 |
|------|----|----|-----|-----|----|----|
| `review_agent/config.py` | ✓ | | | | | ✓ |
| `review_agent/services/section_classifier.py` | | ✓ | | | | ✓ |
| `review_agent/graph/review_graph.py` | | | ✓ | ✓ | | ✓ |
| `review_agent/checkpoint.py` | | | | ✓ | | |
| `review_agent/state/review_state.py` | | | | | ✓ | ✓ |
| `review_agent/pyproject.toml` | | | | ✓ | | |
| `tests/test_config.py` | | | | | | ✓ |
| `tests/test_section_classifier.py` | | | | | | ✓ |
| `tests/test_state_reducers.py` | | | | | | ✓ |

**Not touched:** `document_core`, research agent, Java, frontend, `legal_ai_platform`.

---

## 10. Definition of done

- [x] `SETTINGS_CACHE_TTL_SECONDS` respected; `get_settings.cache_clear()` still works (all 8 existing test call sites)
- [x] Two back-to-back `classify_all_sections` calls use identical batch sizing
- [x] `run_review` × 100 in one process: RSS stable (no `MemorySaver` growth)
- [x] Duplicate warning strings appear once in final `state["warnings"]`
- [x] `pytest tests/test_config.py tests/test_section_classifier.py tests/test_state_reducers.py tests/test_review_e2e.py -q --noconftest` passes
- [ ] (T3b only) `REVIEW_CHECKPOINT_ENABLED=true` + Postgres: `setup()` succeeds; review completes; restart + same `thread_id` can resume (manual smoke)

---

## 11. Implementation order (single PR)

```
T4 (review_state) → T2 (classifier) → T1 (config) → T3a (review_graph) → T5 (tests) → T3b (optional follow-up PR)
```

T4 first — pure additive reducer, zero behavior risk. T3a before T3b.

---

## 12. Out of scope

| Item | Phase |
|------|-------|
| `_config_cap_warned` per-review reset | P34 |
| Prompt template disk cache | P34 |
| Parallel `load_memory` + `contract_parser` | P34 |
| Full async SQLAlchemy in pgvector | P25 follow-up |
| Integration tests against real Postgres checkpoints | P32 |
| FastAPI lifespan wiring for shared checkpointer | Platform (when review API lands) |

---

## 13. Risk register

| Risk | Mitigation |
|------|------------|
| Removing checkpointer breaks unknown resume path | Grep shows no `interrupt` / `get_state` usage in review_agent |
| TTL breaks tests expecting env stickiness | Tests already call `get_settings.cache_clear()` |
| Postgres `setup()` on every review (T3b) | Run inside context manager once per `run_review`; `setup()` is idempotent |
| `langgraph-checkpoint-postgres` not installed | T3b behind optional extra + env flag; T3a is default |
