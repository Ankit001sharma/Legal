# Phase 21 P2-R — Cross-Encoder Reranker (Production Precision)

**Plan ID:** `DR-PHASE-21-P2R-CROSS-ENCODER-RERANKER`  
**Priority:** P2-R (retrieval precision — orthogonal to P2 lexical-first classifier)  
**Impact:** **0 LLM calls**; **better top-10 policy parents** per section → fewer wrong-policy compares and missed playbook hits  
**Accuracy:** ★★★★★ — fixes RC-6 false positives in union retrieval without changing recall paths  
**Depends on:** Phase 10/12 union retrieval (`multi_retrieval.py`), optional `sentence-transformers` (same extra as embeddings)  
**Scope:** `document_core/search/reranker.py`, new `embeddings/reranker_service.py`, `config.py`, tests  
**Non-goals:** New graph nodes, embedding model swap, Cohere/API rerank, NLI gate, compare/guard LLM changes

---

## 0. Problem (root cause — verified in code)

**Symptom:** Section retrieves 20–40 union candidates (dense + FTS + metadata) but **wrong policy parent** lands in top-10 → compare LLM reads irrelevant playbook text → missed violation or weak INCONCLUSIVE.

**Not a recall problem** (Phase 12 P3 fixed retry/union). This is **precision at cut-down**.

### Current reranker — lexical fusion, not cross-encoder

**File:** `document_core/search/reranker.py`

```python
# When reranker_enabled=true (default):
fused = 0.65 * lexical_token_overlap(query, passage) + 0.35 * retrieval_score
```

| Limitation | Effect |
|------------|--------|
| Token overlap ≠ semantic relevance | High hybrid score + low word overlap still wins over better policy |
| No cross-encoder | Cannot rank `"forced labor due diligence"` query against policy prose with different wording |
| Same query used for dense/FTS/rerank | OK — but rerank stage needs **semantic** scoring, not Jaccard-lite |
| Docs say "no-op reranker" | **Stale** — lexical fusion runs today; plan replaces **backend**, not wiring |

### Single call site (good — minimal diff)

**File:** `review_agent/services/multi_retrieval.py` L177–183

```python
reranked = rerank_hits(query, union, top_k=cfg.retrieval_final_top_k, enabled=core.reranker_enabled)
```

No other production callers. **Do not** add rerank calls elsewhere in v1.

### What accuracy requires

1. Cross-encoder scores **(query, policy parent passage)** pairs semantically.  
2. **Fallback** to lexical fusion if model unavailable (CI, dev without `[embeddings]`).  
3. **Never drop** recall candidates before rerank — still rerank full union, then `top_k=10`.  
4. **Truncate** long parent text for CE input — full text stays in compare LLM later.

---

## 1. Design principles

1. **One choke point** — extend `rerank_hits()` only; `multi_retrieval.py` adds ≤5 lines (backend + meta).
2. **Lazy load** — mirror `embeddings/service.py` singleton; no model load at import.
3. **Fail open** — CE load/predict failure → lexical fusion → score sort (never empty hits).
4. **Minimal deps** — reuse optional extra `document-core[embeddings]` (`sentence-transformers` includes `CrossEncoder`).
5. **No async rewrite** — sync `predict()` on ≤50 pairs is acceptable (~50–200ms MiniLM); optional `asyncio.to_thread` only if profiling shows loop block.
6. **Remove confusion** — delete stale "no-op reranker" comments; rename config for clarity.
7. **0 LLM** — rerank is deterministic ML inference only.

---

## 2. Target flow (after P2-R)

```text
multi_retrieve attempt:
  dense + FTS + metadata → union (≤ recall_top_k each, ~20–40 parents)
        │
        ▼
  rerank_hits(query, union, top_k=10)
        │
        ├─ reranker_enabled=false → sort by retrieval score → top 10
        │
        ├─ backend=cross_encoder + model OK
        │     CE.predict([(query, passage)...]) → fuse → top 10
        │
        └─ CE unavailable / error
              → lexical fusion (current behavior) → top 10
```

**Downstream unchanged:** compare LLM receives ≤10 `RetrievalHit` parents per section (same cap).

---

## 3. Config (document_core)

**File:** `document_core/config.py` (+8 lines)

```python
reranker_enabled: bool = True
reranker_backend: Literal["lexical", "cross_encoder"] = "cross_encoder"
reranker_model: str = "BAAI/bge-reranker-v2-m3"
reranker_max_passage_chars: int = 2000
reranker_fusion_retrieval_weight: float = 0.10  # final = 0.90*ce + 0.10*retrieval_score
```

| Env | Default | Notes |
|-----|---------|-------|
| `RERANKER_ENABLED` | `true` | Master switch — `false` = retrieval score only |
| `RERANKER_BACKEND` | `cross_encoder` | `lexical` = keep today's fusion for A/B |
| `RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | Dev/light: `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| `RERANKER_MAX_PASSAGE_CHARS` | `2000` | CE input cap |
| `RERANKER_FUSION_RETRIEVAL_WEIGHT` | `0.10` | Stabilizes rank when CE scores tie |

**Do not** duplicate settings in `review_agent/config.py` — keep reading `get_core_settings()` in `multi_retrieval.py`.

**`.env.example`:** document new vars under existing `RERANKER_ENABLED=true`.

---

## 4. Cross-encoder service (new module)

**File:** `document_core/embeddings/reranker_service.py` (~75 lines)

Mirror `embeddings/service.py` patterns:

```python
@lru_cache(maxsize=1)
def _load_cross_encoder(model_name: str):
    from sentence_transformers import CrossEncoder
    logger.info("loading reranker model: %s", model_name)
    return CrossEncoder(model_name)

def reranker_available() -> bool:
    if not get_settings().reranker_enabled:
        return False
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True

def score_query_passages(
    query: str,
    passages: list[str],
) -> list[float] | None:
    """Return CE scores aligned with passages, or None on failure."""
```

**Rules:**

- Batch all pairs in **one** `model.predict(pairs)` call (not per-hit loop).
- Normalize CE logits to `[0,1]` via sigmoid if needed (CE returns raw logits — use scores as-is for ordering; optional min-max within batch for fusion).
- On any exception: log warning, return `None` (caller falls back to lexical).

**Model choice (accuracy vs latency):**

| Model | Use case |
|-------|----------|
| `BAAI/bge-reranker-v2-m3` | **Production default** — multilingual, strong on legal paraphrase |
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | Local dev / CPU-only fast path |

---

## 5. Reranker refactor (core change)

**File:** `document_core/search/reranker.py` (~40 lines changed, structure preserved)

### 5.1 Passage text for scoring

```python
def _passage_for_rerank(hit: RetrievalHit, *, max_chars: int) -> str:
    parent = hit.parent_chunk
    title = (parent.title or "").strip()
    text = (parent.text or "").strip()[:max_chars]
    if title and text:
        return f"{title}\n{text}"
    return title or text
```

Use **title + body snippet** — matches how lawyers scan playbook sections; better CE signal than body alone.

### 5.2 Scoring backends

```python
def rerank_hits(
    query: str,
    hits: list[RetrievalHit],
    *,
    top_k: int,
    enabled: bool = True,
    backend: Literal["lexical", "cross_encoder"] = "lexical",
    max_passage_chars: int = 2000,
    fusion_retrieval_weight: float = 0.10,
) -> list[RetrievalHit]:
```

| Branch | Behavior |
|--------|----------|
| `not enabled` or empty hits | Sort by `hit.score` desc → `[:top_k]` |
| `backend=cross_encoder` | Try CE scores; on success fuse with retrieval; else fall through |
| `backend=lexical` (or CE failed) | Current `0.65*lex + 0.35*score` fusion |

**Cross-encoder fusion (accuracy-safe):**

```python
ce_norm = _normalize_scores(ce_scores)  # min-max within batch, avoid div0
fused = (1 - w) * ce_norm + w * retrieval_score
```

Keep small `w` (0.10) so semantic rank dominates but high-confidence retrieval isn't fully discarded.

### 5.3 Wire settings in multi_retrieval

**File:** `review_agent/services/multi_retrieval.py` (+4 lines)

```python
reranked = rerank_hits(
    query,
    union,
    top_k=cfg.retrieval_final_top_k,
    enabled=core.reranker_enabled,
    backend=core.reranker_backend,
    max_passage_chars=core.reranker_max_passage_chars,
    fusion_retrieval_weight=core.reranker_fusion_retrieval_weight,
)
step["reranker_backend"] = core.reranker_backend if core.reranker_enabled else "off"
```

Optional: `step["reranker_used"] = "cross_encoder" | "lexical_fallback"` when CE fails.

---

## 6. Code to remove / simplify

| Item | Action |
|------|--------|
| Stale comments "no-op reranker" in plans/code | Update to "lexical fusion default; CE in P2-R" |
| Duplicate rerank logic in `multi_retrieval.py` | **None exists** — keep single call |
| New graph node for rerank | **Do not add** |
| Cohere/API rerank wrapper | **Out of scope** |
| `_lexical_score` helpers | **Keep** — CE fallback path |
| `reranker_enabled` bool | **Keep** — master off switch for tests |

---

## 7. Dependencies & ops

**Install (same as embeddings):**

```powershell
pip install -e "Legal/document_core[embeddings]"
```

**First run:** HuggingFace model download (~400MB–1.2GB depending on model). Cache in HF home.

**Docker / prod:** Pre-bake model in image or mount cache volume; set `RERANKER_MODEL` explicitly.

**CI:** Tests mock CE or set `RERANKER_BACKEND=lexical` — no GPU required in default pytest.

---

## 8. Tests

### 8.1 `document_core/tests/test_reranker.py` (extend)

| Test | Assert |
|------|--------|
| `test_reranker_disabled_preserves_order` | **Keep** — unchanged |
| `test_reranker_lexical_prefers_match` | Rename existing lexical test; `backend="lexical"` |
| `test_cross_encoder_reorders_by_mock_scores` | Mock `score_query_passages` → low retrieval + high CE wins |
| `test_cross_encoder_fallback_to_lexical` | Mock CE returns `None` → lexical path still runs |
| `test_passage_includes_title` | Unit test `_passage_for_rerank` |

### 8.2 `document_core/tests/test_reranker_service.py` (new, ~50 lines)

- Mock `CrossEncoder.predict` — verify batch pairs shape `(N, 2)`.
- `reranker_available()` false when `sentence_transformers` missing (monkeypatch import).

### 8.3 Regression

```powershell
cd Legal\document_core
python -m pytest tests/test_reranker.py tests/test_reranker_service.py -q

cd Legal\review\review_agent
python -m pytest tests/test_multi_retrieval.py -q
python -m pytest tests/ -q --ignore=tests/test_review_e2e.py
```

**Existing** `test_multi_retrieval_merges_three_paths` must pass with default test settings (`RERANKER_BACKEND=lexical` in conftest or env).

---

## 9. E2E verification

### 9.1 Controlled corpus test (recommended before Cisco)

1. Index two policy parents: one **on-topic** liability (low hybrid score), one **off-topic** privacy (high hybrid score due to shared tokens).
2. Query `"limitation of liability cap"`.
3. **Pass:** On-topic parent in **top-1** with `cross_encoder`; lexical may fail this case.

### 9.2 Cisco assessment

```powershell
cd Legal\temp_java_sync
python beta_test/run_cisco_assessment.py
```

| Check | Before | After |
|-------|--------|-------|
| Legal score | 10/10 | **≥ 10/10** |
| Sections with policy hits | 6/6 | **6/6** (recall unchanged) |
| Wrong-policy compare (manual) | occasional | **fewer** — HR/minerals sections hit correct playbook parent |
| Wall-clock | baseline | **+0.5–2s** total (11 sections × ~30 pairs CE) acceptable |
| LLM calls | unchanged | **unchanged** |

### 9.3 Artifact spot check

Per section in `section_retrieval_by_id`:

```python
# Top hit policy categories should align with section categories
bundle.categories[0] in (bundle.policy_hits[0].parent_chunk.metadata or {}).get("categories", [])
# or title contains expected playbook family
```

---

## 10. LLM call accounting

| Change | LLM impact |
|--------|------------|
| Cross-encoder rerank | **0** |
| Better top-10 → fewer wrong compares | **−0 to 2** compare calls (indirect) |
| Fewer INCONCLUSIVE from wrong policy | **−0 to 1** guard calls (indirect) |

**Net:** neutral to slightly fewer downstream LLM calls; **precision win is primary**.

---

## 11. Risk matrix

| Risk | Mitigation |
|------|------------|
| Model load slow / OOM on small VM | Env fallback `RERANKER_BACKEND=lexical`; lighter MiniLM model |
| CE worse than lexical on tiny corpora | Fusion weight keeps retrieval signal; A/B via backend flag |
| Blocking event loop | Batch size ≤50; profile; add `to_thread` only if needed |
| HF download fails in prod | Pre-cache model; lexical fallback |
| Passage truncation hides key phrase | 2000 chars covers most parent sections; title always included |
| Test flakiness with real CE | Mock in unit tests; optional integration marker `@pytest.mark.reranker_live` |

---

## 12. Implementation checklist

- [x] **P2-R.1** `reranker_service.py` — lazy CrossEncoder + `score_query_passages()`
- [x] **P2-R.2** Refactor `reranker.py` — CE path + lexical fallback + `_passage_for_rerank`
- [x] **P2-R.3** `config.py` + `.env.example` — backend, model, passage cap, fusion weight
- [x] **P2-R.4** Wire kwargs in `multi_retrieval._retrieve_attempt` + step meta
- [x] **P2-R.5** Unit tests (mock CE, lexical regression, fallback)
- [x] **P2-R.6** conftest: default `RERANKER_BACKEND=lexical` for CI stability
- [ ] **P2-R.7** Controlled corpus test OR Cisco re-run — top hit accuracy
- [ ] **P2-R.8** Optional: `reranker_backend_used` in `compliance_stats` / artifact ops

---

## 13. Phase 21 sequence

```text
P2 lexical-first classifier ✅ → P1 wrong-section quote ✅
  → **P2-R cross-encoder reranker (this)** → future: routing/keyword table dedupe
```

**Orthogonal to** classifier, guard batch, dedupe, quote grounding — ship independently once union retrieval is stable.

---

## 14. Before / after diagram

```text
TODAY (lexical fusion reranker)
──────────────────────────────
Query: "forced labor human rights"
Union: [privacy_policy score=0.92, hr_policy score=0.55, forced_labor_policy score=0.48]
Lexical overlap: privacy wins (shared tokens "personal", "data") ✗
Compare LLM reads wrong policy → miss or INCONCLUSIVE

AFTER P2-R (cross-encoder)
──────────────────────────
Same union → CE scores semantic relevance
forced_labor_policy → top-1 ✓
Compare LLM reads correct playbook section
```

---

## 15. Files touched (minimal)

| File | Change | Est. lines |
|------|--------|------------|
| `document_core/embeddings/reranker_service.py` | **New** — CE load + batch predict | +75 |
| `document_core/search/reranker.py` | CE backend + passage helper + fusion | +45, −5 |
| `document_core/config.py` | 4 new settings | +8 |
| `document_core/.env.example` | Document vars | +4 |
| `review_agent/services/multi_retrieval.py` | Pass backend settings + meta | +5 |
| `document_core/tests/test_reranker.py` | CE mock + lexical regression | +45 |
| `document_core/tests/test_reranker_service.py` | **New** | +50 |
| `document_core/tests/conftest.py` | Default `RERANKER_BACKEND=lexical` for CI | +5 |

**Total:** ~235 lines. **No new graph nodes. No review_agent config duplication.**

---

*End of Phase 21 P2-R plan — cross-encoder reranker with lexical fallback.*
