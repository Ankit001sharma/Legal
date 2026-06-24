# Phase 34 — Performance Polish

**Plan ID:** `DR-PHASE-34-PERFORMANCE`  
**Priority:** P3  
**Duration:** ~4–5 days  
**Depends on:** Phase 25 (async/executor), Phase 30 (stable state)  
**Non-goals:** Full graph rewrite, new embedding models, GPU serving

---

## 1. Goal

Reduce **per-review latency** and **disk I/O** with small targeted wins after production blockers are closed.

---

## 2. Root causes

| # | Root cause | Location | Cost |
|---|------------|----------|------|
| R1 | Prompt `.md` files read every LLM call | routing, classifier, compare, guard, final_verify | ~40 disk reads/review |
| R2 | Linear graph — `load_memory` then `parser` sequential | `review_graph.py` L66–79 | Wasted wall time |
| R3 | Sync `embed_documents` in ingest | `pgvector_store.py` | Blocks on large policies |
| R4 | `_config_cap_warned` global — minor | `config.py` | Misleading ops signal |

---

## 3. Task map

| # | Task | Est. | Files | Risk |
|---|------|------|-------|------|
| **T1** | Cache prompt templates | 2h | 5 service files | Low |
| **T2** | Parallel memory + parser (optional) | 1d | `review_graph.py` | Med |
| **T3** | Embedding batch in executor | 4h | `pgvector_store.py` | Low |
| **T4** | Per-review config warning | 1h | `config.py` | Low |
| **T5** | Benchmark before/after | 4h | `temp_java_sync` benchmark | Low |

---

## 4. T1 — Prompt template cache (minimal)

Each file has `_load_prompt_template()` — add shared helper:

```python
# review_agent/prompts/loader.py (NEW)
from functools import lru_cache

@lru_cache(maxsize=16)
def load_prompt_pair(name: str) -> tuple[str, str]:
    ...
```

Replace 5 duplicate loaders with one import. **~20 LOC net reduction.**

Invalidate in tests: `load_prompt_pair.cache_clear()`.

---

## 5. T2 — Parallel graph fan-out (optional flag)

**Env:** `REVIEW_PARALLEL_MEMORY_PARSER=false` (default off)

When true:
```python
graph.add_edge(START, "load_memory")
graph.add_edge(START, "contract_parser")
graph.add_edge(["load_memory", "contract_parser"], "clause_detection")
```

**Risk:** `clause_detection` must not depend on memory output today — verify in `nodes.py` before enabling default.

Ship behind flag; enable after integration test.

---

## 6. T3 — Embedding batch (if not done in P25)

```python
embeddings = await asyncio.to_thread(embed_documents, child_texts)
```

Or batch size 32 inside `embed_documents` — measure 50-section policy ingest time.

---

## 7. T4 — Config warning

Move `_config_cap_warned` to `build_runtime_settings_snapshot()` — warn once **per review** in artifact metadata, not per process.

---

## 8. Definition of done

- [ ] Prompt loader: 0 disk reads after first review in process
- [ ] Benchmark: ≥10% wall-time reduction on Acme NDA with parallel flag on (document baseline)
- [ ] Default graph unchanged when flag off
- [ ] All unit tests green

---

## 9. Out of scope

- Section-level parallel compare (large graph change)
- Caching LLM responses
- CDN / static asset optimization (no frontend)
