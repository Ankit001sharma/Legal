# Phase 22 P5 — Config / Env Production Alignment

**Plan ID:** `DR-PHASE-22-P5-CONFIG-ENV-ALIGNMENT`  
**Priority:** P5 (ops / deploy fidelity — does not change graph topology)  
**Impact:** **0 LLM calls**; reproducible enterprise tuning; **observable rerank + discovery caps** in prod/benchmarks  
**Depends on:** Phase 22 P1 (adaptive discovery), P4 (compare hit selection), Phase 21 P2-R (reranker), Phase 21 P0 (LLM rate-limit)  
**Scope:** `config.py`, `.env.example` (review + document_core + temp_java_sync), `bootstrap_env.py`, `section_retrieval_nodes.py`, `multi_retrieval.py`, `review_artifact.py`, `run_scale_benchmark.py`, tests  
**Non-goals:** New graph nodes, discovery algorithm rewrite, reranker model swap, Java API changes, secrets management platform

---

## 0. Verified root cause (code + deploy + scale benchmark)

### Symptom → production impact

```text
Deploy / benchmark loads ReviewSettings from .env + code defaults
  → stale or partial .env (dev Cisco profile)
  → scale benchmark uses config.py defaults (not .env.example enterprise profile)
  → discovery flat cap 50 (code default) vs 0 (example = group-only cap)
  → missing SECTION_CLASSIFY_MODE, guard batch, rate-limit, P4 compare keys in dev .env
  → reranker runs per section but reranker_used never in compliance_stats / artifact ops
  → ops cannot verify top-hit quality or which config profile ran
```

**Enterprise 40+ context:** System behaves like **Cisco 6-family ESG** when env is empty/stale, even after P1 adaptive group cap — because **flat policy cap**, **missing explicit classify/compare/guard settings**, and **benchmark env bootstrap** do not load the enterprise profile.

### Evidence (verified in repo)

| Source | Setting | Value | Implication |
|--------|---------|-------|-------------|
| `config.py` L44 | `discovery_max_policies` default | **50** | Hard flat cap after grouping |
| `review_agent/.env.example` L16 | `DISCOVERY_MAX_POLICIES` | **0** | **0 = no flat cap** (`_apply_flat_cap` / `_select_grouped_policies` skip when `<= 0`) |
| `review_agent/.env` (dev) | `DISCOVERY_MAX_POLICIES` | **50** | Matches wrong code default; contradicts example |
| `review_agent/.env` (dev) | `REVIEW_POLICY_SOURCE` | `request` | **Invalid key** — `ReviewSettings` has `review_policy_scope`; pydantic `extra=ignore` → silently ignored |
| `review_agent/.env` (dev) | Discovery/classify/guard/P4 keys | **missing** | Falls back to code defaults |
| `temp_java_sync/bootstrap_env.py` L27–38 | Loads from `review_agent/.env` | **LLM_* only** | Benchmark does **not** inherit discovery/classify/compare settings |
| `temp_java_sync/.env.example` | Discovery block | **absent** | E2E runs with code defaults unless manually set |
| `run_scale_benchmark.py` L120 | `get_settings.cache_clear()` | yes | Re-reads env — but env lacks enterprise keys |
| Scale summary (`scale_benchmark_summary.json`) | `discovery_returned` | **19** / contract | Adaptive group cap working (P1); benchmark still flags `>6 cap expected` (stale Cisco heuristic) |
| `multi_retrieval.py` L195–197 | `reranker_used` | per-attempt step only | Buried in `retrieval_meta.attempts[]` |
| `section_retrieval_nodes.py` L84–108 | `compliance_stats` rollup | no rerank fields | **Not exported to ops** |
| `review_artifact.py` `_build_ops` | ops fields | no rerank / settings | Cannot audit rerank in prod report |

### Root causes (precise)

| # | Root cause | File / mechanism | Effect |
|---|------------|------------------|--------|
| **RC-1** | **Code default ≠ documented deploy default** | `config.py` `discovery_max_policies=50` vs `.env.example` `0` | Deployers omit key → accidental flat cap; example says “unlimited after groups” |
| **RC-2** | **Dual cap semantics undocumented in one place** | `policy_discovery.py` `_select_grouped_policies`: `max_groups` then `max_policies` | Ops unclear which cap bound scale `discovery_returned=19` |
| **RC-3** | **Dev `.env` stale / partial** | `review_agent/.env` missing P1/P4/guard/rate-limit keys | Local prod ≠ enterprise intent |
| **RC-4** | **Invalid env key silently ignored** | `REVIEW_POLICY_SOURCE` not in schema | Thinks scope is `request`; actually `discovered` (default) |
| **RC-5** | **Benchmark bootstrap loads LLM only** | `bootstrap_env.py` `setdefault` for `LLM_*` | Scale tests **not** representative of `.env.example` enterprise profile |
| **RC-6** | **No resolved settings snapshot in run output** | `compliance_stats` has discovery counts but not cap mode / classify mode / compare mode | Cannot reproduce or diff runs |
| **RC-7** | **Reranker ops gap** | `reranker_used` in attempt step; not copied to bundle top-level or stats rollup | “Reranker runs but not in ops” |
| **RC-8** | **Two `.env` files, one process** | `ReviewSettings` reads `review_agent/.env`; `DocumentCoreSettings` reads `document_core/.env` | Reranker/env split — easy to enable CE in code path but never verify in review artifact |

**Already correct (do not re-implement):**

| Fix | Status |
|-----|--------|
| Adaptive group cap (P1) | `resolve_discovery_group_cap()` in `policy_discovery.py` |
| Category-aligned compare (P4) | `compare_hit_selection.py` |
| Cross-encoder reranker (P2-R) | `document_core/search/reranker.py` wired in `multi_retrieval.py` |
| LLM 429 backoff | `llm_gateway.invoke_structured` |

---

## 1. Design principles (minimal production patch)

1. **Single source of truth for defaults** — `config.py` defaults must match `.env.example` semantics (especially `discovery_max_policies=0`).
2. **0 new graph nodes** — snapshot + rerank rollup in existing nodes only.
3. **No secrets in snapshot** — export resolved **non-secret** settings only (no API keys, DB URLs).
4. **Profile, don’t fork code** — document **two env profiles** (enterprise / cisco-small) in `.env.example` comments; no runtime profile enum in v1.
5. **Benchmark = deploy** — `bootstrap_env` + scale runner load the same discovery/classify/compare keys as production example.
6. **Observability over guessing** — every review report includes `runtime_settings` + `reranker_*` ops counts.
7. **Backward compatible** — changing default `discovery_max_policies` 50→0 widens discovery slightly for callers relying on code default only; enterprise-correct.

---

## 2. Target behavior (after P5)

```text
Process start
  → ReviewSettings + DocumentCoreSettings loaded
  → runtime_settings snapshot → compliance_stats (non-secret)
        │
        ▼
policy_discovery_node
  → discovery_meta includes group_cap_resolved, max_policies_effective
        │
        ▼
section_policy_retrieval_node
  → per-section reranker_used copied to bundle.retrieval_meta top-level
  → rollup: reranker_sections_cross_encoder | lexical_fallback | off
        │
        ▼
report / artifact ops
  → ops.reranker_cross_encoder_sections, ops.runtime_settings_hash (optional)
  → scale benchmark JSON includes settings_snapshot
```

**Lawyer-visible:** unchanged. **Ops-visible:** can answer “which caps and backends ran?”

---

## 3. Implementation tasks

### P5-1. Align `discovery_max_policies` default (~2 lines + test)

**File:** `review_agent/config.py`

```python
discovery_max_policies: int = 0  # 0 = no flat cap; group cap only (see discovery_max_policy_groups*)
```

**Semantics (document in field docstring):**

| Value | Behavior |
|-------|----------|
| `0` | No flat cap after grouping — **enterprise default** |
| `>0` | Additional flat cap on grouped winners (legacy / safety valve) |

**Acceptance:** Fresh `ReviewSettings()` without env → `discovery_max_policies == 0`.

---

### P5-2. Settings snapshot helper (~45 lines)

**File:** `review_agent/config.py` (or `review_agent/services/runtime_settings.py` if prefer separation)

```python
def build_runtime_settings_snapshot(
    review: ReviewSettings | None = None,
    core: DocumentCoreSettings | None = None,
) -> dict[str, str | int | float | bool]:
    """Non-secret resolved settings for ops reproducibility."""
```

**Include (minimum):**

| Key | Source |
|-----|--------|
| `review_policy_scope` | review |
| `discovery_group_mode`, `discovery_group_cap_mode` | review |
| `discovery_max_policy_groups`, `discovery_min_policy_groups`, `discovery_max_policy_groups_ceiling` | review |
| `discovery_max_policies`, `discovery_max_topics_ceiling` | review |
| `section_classify_mode` | review |
| `compare_policy_hit_mode`, `compare_max_policy_hits` | review |
| `guard_pass_enabled`, `guard_pass_batch_size` | review |
| `llm_global_concurrency`, `llm_rate_limit_max_retries` | review |
| `retrieval_final_top_k`, `retrieval_category_hard_filter` | review |
| `reranker_enabled`, `reranker_backend` | core |

**Wire:** `review_graph.py` init or first node (`contract_routing_node`) merges into `compliance_stats["runtime_settings"]`.

**Exclude:** `llm_api_key`, `database_url`, `policy_catalog_url` values (omit or redact).

---

### P5-3. Env template alignment — enterprise vs Cisco profiles (~60 lines comments + keys)

**Files:**

- `review_agent/.env.example` — add **explicit** (uncommented) blocks already partially present:
  - Guard: `GUARD_PASS_ENABLED`, `GUARD_PASS_BATCH_SIZE`, `GUARD_PASS_CONCURRENCY`
  - Rate limit: already present — add one-line comment “required for Mistral scale”
  - P4 compare block — already present
  - **New:** `REVIEW_POLICY_SCOPE=discovered` (replace invalid `REVIEW_POLICY_SOURCE` in dev docs)

- Add profile comment blocks:

```ini
# --- Profile: enterprise (20-section × 40+ playbooks) — DEFAULT for scale benchmark ---
# DISCOVERY_MAX_POLICIES=0
# DISCOVERY_GROUP_CAP_MODE=adaptive
# DISCOVERY_MAX_POLICY_GROUPS_CEILING=20
# SECTION_CLASSIFY_MODE=lexical_first
# COMPARE_POLICY_HIT_MODE=category_aligned

# --- Profile: cisco-small (≤8 sections, single ESG family) ---
# DISCOVERY_GROUP_CAP_MODE=fixed
# DISCOVERY_MAX_POLICY_GROUPS=6
# DISCOVERY_MAX_POLICIES=0
```

- `document_core/.env.example` — add comment: “Review agent reads reranker from **this** file when MCP runs in-process; align with review deploy.”

- `temp_java_sync/.env.example` — add **forwarding block** (copy-paste from review example, non-secret keys only):

```ini
# Review pipeline (non-LLM) — must match review_agent/.env.example for faithful E2E
DISCOVERY_MAX_POLICIES=0
DISCOVERY_GROUP_CAP_MODE=adaptive
SECTION_CLASSIFY_MODE=lexical_first
COMPARE_POLICY_HIT_MODE=category_aligned
GUARD_PASS_ENABLED=true
LLM_GLOBAL_CONCURRENCY=2
```

**Dev `.env` hygiene (manual, documented in plan §6):** Sync local `review_agent/.env` from example; remove `REVIEW_POLICY_SOURCE`; set `DISCOVERY_MAX_POLICIES=0`.

---

### P5-4. Benchmark bootstrap loads review settings (~15 lines)

**File:** `temp_java_sync/bootstrap_env.py`

Extend fallback load from `review_agent/.env` beyond `LLM_*` to prefix allowlist:

```python
_REVIEW_ENV_PREFIXES = (
    "DISCOVERY_", "SECTION_", "COMPARE_", "RETRIEVAL_",
    "GUARD_", "LLM_", "GAP_", "FINAL_", "REVIEW_",
    "ENFORCE_", "FINDING_", "PLAYBOOK_", "GROUNDING_",
)
```

Use `setdefault` (benchmark env wins if explicitly set).

**Acceptance:** Scale run with empty `temp_java_sync/.env` still picks up enterprise keys from `review_agent/.env.example` if copied, or from review `.env` for prefixed keys.

---

### P5-5. Reranker ops export (~35 lines)

**5a. Copy winning rerank fields to bundle top-level**

**File:** `multi_retrieval.py` (~5 lines after L297)

```python
if winning_step.get("reranker_used"):
    paths["reranker_used"] = winning_step["reranker_used"]
if winning_step.get("reranker_backend"):
    paths["reranker_backend"] = winning_step["reranker_backend"]
```

**5b. Roll up in retrieval node**

**File:** `section_retrieval_nodes.py`

Aggregate across bundles:

```python
reranker_cross_encoder_sections = 0
reranker_lexical_fallback_sections = 0
reranker_off_sections = 0
for bundle in bundles.values():
    used = (bundle.retrieval_meta or {}).get("reranker_used")
    ...
```

Add to `compliance_stats`:

```python
"reranker_cross_encoder_sections": ...,
"reranker_lexical_fallback_sections": ...,
"reranker_off_sections": ...,
"reranker_backend_config": core.reranker_backend if core.reranker_enabled else "off",
```

**5c. Artifact ops**

**File:** `review_artifact.py` + `schemas/review_artifact.py`

Add optional fields to `ReviewArtifactOps`:

- `reranker_cross_encoder_sections: int = 0`
- `reranker_lexical_fallback_sections: int = 0`

Wire in `_build_ops()`.

---

### P5-6. Discovery meta clarity (~10 lines)

**File:** `policy_discovery.py` (end of `discover_policies_from_topics`)

Ensure `discovery_meta` always includes:

```python
"discovery_max_policies_effective": settings.discovery_max_policies,
"discovery_group_cap_resolved": group_cap,
```

(already has `discovery_group_cap_resolved` — verify; add flat cap effective if missing)

**File:** `run_scale_benchmark.py` L167–168

Replace stale heuristic:

```python
# Before: discovery_returned > 6 always flagged
# After: flag only if discovery_returned > stats.get("discovery_group_cap_resolved", 6) + 2
#        OR discovery_capped and returned << ranked
```

---

### P5-7. Optional startup config warning (~20 lines)

**File:** `review_agent/config.py` or `review_graph.py`

On first `get_settings()`, log **once** (INFO):

- If `discovery_max_policies > 0` and `discovery_group_cap_mode == "adaptive"`:  
  `"discovery_max_policies=%s applies flat cap after group cap; enterprise deploys typically use 0"`

Do **not** fail startup — warn only.

---

### P5-8. Scale benchmark settings snapshot (~15 lines)

**File:** `run_scale_benchmark.py`

After `get_settings.cache_clear()`:

```python
from review_agent.config import build_runtime_settings_snapshot
settings_snapshot = build_runtime_settings_snapshot()
```

Include in each contract result + summary root:

```json
"runtime_settings": { ... }
```

---

## 4. File touch list

| File | Change | Est. lines |
|------|--------|------------|
| `review_agent/config.py` | Default `discovery_max_policies=0`; snapshot helper | +50 |
| `review_agent/.env.example` | Profiles + explicit guard/rate-limit | +35 |
| `document_core/.env.example` | Cross-ref comment | +3 |
| `temp_java_sync/.env.example` | Enterprise review keys | +12 |
| `temp_java_sync/bootstrap_env.py` | Prefix allowlist load | +15 |
| `multi_retrieval.py` | Top-level reranker meta | +5 |
| `graph/section_retrieval_nodes.py` | Rerank rollup stats | +25 |
| `services/review_artifact.py` | Ops fields | +10 |
| `schemas/review_artifact.py` | Ops schema | +4 |
| `graph/review_graph.py` or `discovery_nodes.py` | Inject runtime_settings | +8 |
| `temp_java_sync/beta_test/run_scale_benchmark.py` | Snapshot + cap heuristic | +20 |
| `tests/test_runtime_settings.py` | **New** | +40 |
| `tests/test_section_retrieval_warnings.py` or new | Rerank rollup | +35 |

**Total:** ~260 lines (incl. tests). **No graph topology change.**

---

## 5. Tests (must pass)

| Test | Setup | Assert |
|------|-------|--------|
| `test_default_discovery_max_policies_zero` | Empty env | `ReviewSettings().discovery_max_policies == 0` |
| `test_runtime_settings_snapshot_redacts_secrets` | Env with `LLM_API_KEY` | Snapshot omits key |
| `test_runtime_settings_snapshot_includes_compare_mode` | `COMPARE_POLICY_HIT_MODE=category_aligned` | Snapshot contains key |
| `test_retrieval_node_rerank_rollup` | Mock bundles with `reranker_used=cross_encoder` / `lexical_fallback` | Stats counts correct |
| `test_multi_retrieval_copies_reranker_to_top_level` | Mock `_retrieve_attempt` step | `bundle.retrieval_meta["reranker_used"]` set |
| `test_bootstrap_env_loads_discovery_prefix` | Temp review `.env` with `DISCOVERY_MAX_POLICIES=0` | `os.environ` set after bootstrap |
| **Regression** | Full suite excl. e2e | 190+ pass |

```powershell
cd Legal\review\review_agent
python -m pytest tests/test_runtime_settings.py tests/test_policy_discovery.py tests/test_section_retrieval_warnings.py -q
```

---

## 6. Verification (E2E)

| Run | Before P5 | Target after P5 |
|-----|-----------|-----------------|
| Scale benchmark | Uses code default `discovery_max_policies=50` if env empty | Summary includes `runtime_settings`; `DISCOVERY_MAX_POLICIES=0` |
| `discovery_returned` on 43-policy tenant | 19 (adaptive) | Same or higher; `discovery_capped` explainable via meta |
| Report `artifact.ops` | No rerank fields | `reranker_cross_encoder_sections > 0` when CE enabled |
| Dev `.env` sync | Stale / invalid keys | Matches `.env.example` enterprise block |
| Cisco 6-section E2E | 6/6 pass | **No regression** (fixed profile still 6 groups) |

```powershell
cd Legal\temp_java_sync
python beta_test\run_scale_benchmark.py
# Inspect outputs/scale_benchmark/scale_benchmark_summary.json → runtime_settings, reranker_* ops
```

**Acceptance query:**

```python
stats = state["compliance_stats"]
assert "runtime_settings" in stats
assert stats.get("reranker_cross_encoder_sections", 0) + stats.get("reranker_lexical_fallback_sections", 0) > 0
```

---

## 7. Rollout / risk

| Risk | Mitigation |
|------|------------|
| Default 50→0 widens discovery for deploys with no env | Intended enterprise fix; Cisco profile sets fixed group cap 6 |
| Snapshot leaks config | Explicit allowlist; no secrets |
| bootstrap_env overrides intentional benchmark overrides | `setdefault` only — explicit temp_java_sync env wins |
| Extra stats payload size | ~30 keys JSON — negligible |

**Rollback:** Revert default to 50 only if a tenant relied on accidental flat cap; ops can set `DISCOVERY_MAX_POLICIES=50` explicitly.

---

## 8. Implementation checklist

- [x] **P5-1** Align `discovery_max_policies` default to `0`
- [x] **P5-2** `build_runtime_settings_snapshot()` + wire to `compliance_stats`
- [x] **P5-3** `.env.example` profiles (review + document_core + temp_java_sync)
- [x] **P5-4** `bootstrap_env` prefix allowlist
- [x] **P5-5** Reranker ops export (multi_retrieval + node + artifact)
- [x] **P5-6** Discovery meta + scale benchmark cap heuristic
- [x] **P5-7** Optional startup cap warning log
- [x] **P5-8** Scale benchmark settings snapshot
- [x] **P5-9** Unit tests green (196/196 excl. e2e)
- [ ] **P5-10** Scale + Cisco E2E with synced env

---

## 9. Recommended deploy profiles (copy-paste)

### Enterprise (40+ playbooks, 15–20 sections)

```ini
REVIEW_POLICY_SCOPE=discovered
DISCOVERY_MAX_POLICIES=0
DISCOVERY_GROUP_CAP_MODE=adaptive
DISCOVERY_MIN_POLICY_GROUPS=6
DISCOVERY_MAX_POLICY_GROUPS_CEILING=20
DISCOVERY_SECTION_CATEGORY_SWEEP=true
SECTION_CLASSIFY_MODE=lexical_first
COMPARE_POLICY_HIT_MODE=category_aligned
COMPARE_MAX_POLICY_HITS=3
GUARD_PASS_ENABLED=true
GUARD_PASS_BATCH_SIZE=4
LLM_GLOBAL_CONCURRENCY=2
LLM_RATE_LIMIT_MAX_RETRIES=3
RERANKER_ENABLED=true
RERANKER_BACKEND=cross_encoder
```

### Cisco-small (≤8 sections, single supplier ESG pack)

```ini
DISCOVERY_MAX_POLICIES=0
DISCOVERY_GROUP_CAP_MODE=fixed
DISCOVERY_MAX_POLICY_GROUPS=6
SECTION_CLASSIFY_MODE=lexical_first
COMPARE_POLICY_HIT_MODE=category_aligned
COMPARE_MAX_POLICY_HITS=3
```

---

## 10. Relationship to prior plans

| Plan | Overlap | P5 action |
|------|---------|-----------|
| Phase 22 P1 discovery scope | Adaptive caps | Document + snapshot; fix flat cap default |
| Phase 22 P4 compare quality | Compare env keys | Ensure in example + snapshot |
| Phase 21 P2-R reranker | CE backend | Export `reranker_used` to ops (closes P2-R.8) |
| Phase 21 P0 rate limit | LLM concurrency | Explicit in `.env.example` + snapshot |

**P5 completes Phase 22 ops stack:** P1–P4 fix behavior on covered/gap sections; **P5 makes deploy + benchmark match that behavior.**

---

*End of Phase 22 P5 plan — config / env production alignment.*
