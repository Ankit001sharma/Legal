# Phase 22 P1 — Discovery Scope Fix (Enterprise 40+ Playbooks)

**Plan ID:** `DR-PHASE-22-P1-DISCOVERY-SCOPE`  
**Priority:** P1 (accuracy blocker — ~48% section silence on scale benchmark)  
**Impact:** **−0 LLM calls**; **+40–50% section coverage** on 20-section × 43-policy tenants  
**Depends on:** Phase 21 P2-G (grouping — keep), Phase 6 discovery, Phase 10 taxonomy  
**Scope:** `policy_discovery.py`, `discovery_nodes.py`, `contract_routing.py`, `config.py`, tests  
**Non-goals:** New graph nodes, LLM discovery, removing P2-G grouping, section-first retrieval rewrite

---

## 0. Verified root cause (code + scale benchmark)

### Symptom → mechanism chain

```text
43 policies indexed
  → discover_policies_from_topics(topics[:8], contract_type=<niche>)
  → search_policy filters out policies where applies_to_contract_types ≠ contract_type
  → few/zero hits per topic on OEM/logistics/prof_services
  → aggregate → group by category → cap at discovery_max_policy_groups=6
  → 1–6 document IDs in scope
  → section retrieval category filter ∩ scope → empty for most sections
  → compare skipped → INSUFFICIENT_POLICY_CONTEXT (~9.6 sections/contract)
```

### Evidence (scale benchmark 12×43)

| Metric | Value | Implication |
|--------|-------|-------------|
| `avg_coverage_pct` | **52.1%** | Half of sections silent |
| `discovered_policies` | 6 (MSA) / **1** (niche types) | Cap + contract_type filter |
| `discovery_deduped` | 8 on good runs | Grouping works; **cap too low** |
| Cisco 6-section | 5–6 discovered | P2-G correct for **small ESG** contracts |

### Root causes (precise)

| # | Root cause | File / setting | Effect |
|---|------------|----------------|--------|
| **RC-1** | **Fixed group cap = 6** | `config.py` `discovery_max_policy_groups=6` | 37/43 playbook families dropped after grouping |
| **RC-2** | **Topic cap = 8** | `policy_discovery.py` `_cap_topics(..., max_topics=8)` | Routing emits 15+ topics; only 8 searched |
| **RC-3** | **`contract_type` hard filter at discovery search** | `search.py` `_child_matches_filters` L235–237; passed via `SearchRequest(contract_type=...)` in `discover_policies_from_topics` | Niche types (`professional_services`, `oem`, `logistics`) exclude policies tagged `msa`/`vendor`/`saas` only → **1 group survives** |
| **RC-4** | **Routing vocabulary commercial-biased** | `contract_routing.py` `_TOPIC_KEYWORDS` (9 patterns); `routing_topic_hints.yaml` missing ESG/security/minerals | Discovery never searches for HR, minerals, environment, MSS families |
| **RC-5** | **Discovery is topic-only; section categories unknown** | Graph order: routing → discovery → … → classify at retrieval | Contract has 20 section categories; discovery doesn't use them (0 LLM path available via lexical) |
| **RC-6** | **Scoped retrieval cannot recover** | `section_retrieval_nodes.py` `scope_ids = discovered_policy_document_ids` | Missing family at discovery → **no second chance** for that document ID |

**Production impact:** Pipeline optimized for **Cisco 6-family supplier** reviews. **Enterprise open-contract** review (40+ playbooks, 15–50 sections) loses half the contract to silence.

---

## 1. Design principles (minimal patch)

1. **Keep P2-G grouping** — one winner per category family (dedupe 5× liability variants).
2. **Adaptive cap, not flat 6** — scale cap with contract size; preserve 6 for ≤8 reviewable sections.
3. **Fail-open contract_type at discovery** — mirror `retrieval_category_filter_fallback` (already in multi_retrieval).
4. **Section-lexical category sweep** — 0 LLM; reuse `infer_lexical_classify` / `infer_categories_from_section`.
5. **Union, then group, then cap** — topic hits ∪ category hits; then `_select_grouped_policies`.
6. **No new graph node** — extend `policy_discovery_node` + `discover_policies_from_topics` signature.
7. **Explicit policies unchanged** — skip when `policy_document_ids` / inline policies in request.

---

## 2. Target flow (after P1)

```text
contract_routing → topics[]
        │
        ▼
policy_discovery_node
  ├─ lexical section scan → unique categories[]     [NEW — RC-5]
  ├─ topic search (cap = adaptive max_topics)         [CHANGED — RC-2]
  │     └─ contract_type filter → fallback if sparse [NEW — RC-3]
  ├─ category metadata sweep (list_policy_ids_by_categories) [NEW — RC-5]
  ├─ union + group by policy_group
  └─ cap groups = adaptive(reviewable_sections, unique_categories) [NEW — RC-1]
        │
        ▼
scope_document_ids → section retrieval (unchanged)
```

---

## 3. Implementation tasks

### P1-1. Adaptive group cap (RC-1)

**File:** `review_agent/config.py`

```python
discovery_group_cap_mode: Literal["fixed", "adaptive"] = "adaptive"
discovery_max_policy_groups: int = 6          # fixed mode + floor for adaptive
discovery_max_policy_groups_ceiling: int = 20 # adaptive hard ceiling
discovery_min_policy_groups: int = 6          # Cisco floor
```

**File:** `review_agent/services/policy_discovery.py` — new helper (~25 lines)

```python
def resolve_discovery_group_cap(
    *,
    settings: ReviewSettings,
    reviewable_section_count: int,
    unique_category_count: int,
) -> int:
    if settings.discovery_group_cap_mode == "fixed":
        return settings.discovery_max_policy_groups
    if settings.discovery_max_policy_groups <= 0:
        return 0  # unlimited
    target = max(
        settings.discovery_min_policy_groups,
        unique_category_count,
        (reviewable_section_count + 1) // 2,
    )
    return min(target, settings.discovery_max_policy_groups_ceiling)
```

| Reviewable sections | Unique categories | Cap (adaptive) |
|--------------------|-------------------|------------------|
| 6 (Cisco) | 6 | **6** |
| 20 (scale MSA) | ~15 | **15** |
| 4 (NDA) | 4 | **6** (floor) |

**Wire:** pass `reviewable_section_count` and `unique_category_count` into `discover_policies_from_topics`.

**Acceptance:** Scale MSA discovers **≥12 groups** (not 6); Cisco still **≤6–7**.

---

### P1-2. Adaptive topic cap (RC-2)

**File:** `config.py`

```python
discovery_max_topics: int = 8  # keep for fixed/small tenants
discovery_topic_cap_mode: Literal["fixed", "adaptive"] = "adaptive"
discovery_max_topics_ceiling: int = 20
```

**Logic:** `max_topics = min(ceiling, max(8, len(topics)))` when adaptive; else existing `_cap_topics`.

**Acceptance:** 20-section contract searches **≥15 routing topics**, not 8.

---

### P1-3. Contract-type fail-open at discovery (RC-3) — **highest leverage for niche types**

**File:** `policy_discovery.py`

```python
discovery_contract_type_filter: bool = True
discovery_contract_type_fallback_min_hits: int = 4  # retry without filter if grouped < 4
```

**Algorithm** (inside `discover_policies_from_topics`, ~30 lines):

```text
1. aggregated = _search_all_topics(..., contract_type=ct if filter else None)
2. grouped = _select_grouped_policies(aggregated, max_groups=cap)
3. if filter and len(grouped) < fallback_min_hits:
       aggregated_fallback = _search_all_topics(..., contract_type=None)
       aggregated = _merge_aggregated(aggregated, aggregated_fallback)
       re-group + cap
       warnings.append("discovery contract_type filter relaxed (sparse hits)")
```

**Note:** Policies with **empty** `applies_to_contract_types` already pass filter (`search.py` L236–237). Problem is policies tagged `["msa","vendor"]` only when contract is `professional_services`.

**Acceptance:** `professional_services` / `oem` / `logistics` scale contracts discover **≥8 groups** (was 1).

---

### P1-4. Section-lexical category sweep (RC-5) — 0 LLM

**File:** `policy_discovery.py` — new async helper (~45 lines)

```python
async def _discover_by_section_categories(
    client,
    *,
    tenant_id,
    sections: list[IndexedChunk],
    contract_type: str | None,
    settings: ReviewSettings,
) -> dict[str, DiscoveredPolicy]:
    """Lexical categories from contract sections → list_policy_ids_by_categories."""
```

**Steps:**
1. For each reviewable section: `infer_lexical_classify(section)` or `infer_categories_from_section`.
2. `unique_categories = normalize_categories(flatten)`; drop `general`.
3. For each category: `client.list_policy_ids_by_categories(tenant, [cat], contract_type=...)`.
4. For each doc_id: lightweight `search_policy` query = category query term (from `_CATEGORY_QUERY_TERMS`) to score parent; build `DiscoveredPolicy`.

**Merge:** `aggregated[doc_id] = _build_discovered_policy(...)` — same as topic path.

**File:** `discovery_nodes.py` — pass sections into discovery:

```python
sections = filter_review_sections(state.get("contract_sections") or [], ...)
discovered, warnings, meta = await discover_policies_from_topics(
    ...,
    contract_sections=sections,  # NEW kwarg
)
```

**Acceptance:** MSA with § on minerals/HR/security gets those families in scope even if routing topics miss them.

---

### P1-5. Routing vocabulary expansion (RC-4)

**File:** `contract_routing.py` — extend `_TOPIC_KEYWORDS` (~12 lines)

```python
(r"human rights|forced labor|modern slavery", "human rights forced labor"),
(r"responsible minerals|conflict minerals|\b3tg\b", "responsible minerals"),
(r"\benvironment\b|ghg|greenhouse|cdp|sustainability", "environment GHG reporting"),
(r"master security|\bmss\b|supply chain security", "information security MSS"),
(r"business continuity|\bbcp\b|supply chain visibility|\bscv\b", "business continuity SCV"),
(r"service level|\bsla\b|uptime", "service level agreement"),
(r"insurance|cyber liability", "insurance requirements"),
(r"payment|invoic", "payment terms"),
(r"code of conduct|\brba\b", "supplier code of conduct"),
```

**File:** `prompts/routing_topic_hints.yaml` — add matching ESG/security/ops phrases (mirror above).

**Acceptance:** Lexical routing on scale contracts emits **≥12 topics** including ESG.

---

### P1-6. Discovery meta + warnings (ops)

**Extend `discovery_meta`:**

```python
"discovery_group_cap_resolved": 15,
"discovery_group_cap_mode": "adaptive",
"discovery_contract_type_relaxed": True,
"discovery_category_sweep_added": 4,
"discovery_topics_searched": 16,
```

**Acceptance:** Cisco/scale artifact shows cap reasoning for prod debug.

---

## 4. Config / `.env.example` (production defaults)

```env
# Phase 22 P1 — enterprise discovery scope
DISCOVERY_GROUP_CAP_MODE=adaptive
DISCOVERY_MAX_POLICY_GROUPS=6
DISCOVERY_MIN_POLICY_GROUPS=6
DISCOVERY_MAX_POLICY_GROUPS_CEILING=20
DISCOVERY_TOPIC_CAP_MODE=adaptive
DISCOVERY_MAX_TOPICS=8
DISCOVERY_MAX_TOPICS_CEILING=20
DISCOVERY_CONTRACT_TYPE_FILTER=true
DISCOVERY_CONTRACT_TYPE_FALLBACK_MIN_HITS=4
DISCOVERY_GROUP_MODE=category
DISCOVERY_MAX_POLICIES=0
```

**Cisco-only deploy (optional override):**

```env
DISCOVERY_GROUP_CAP_MODE=fixed
DISCOVERY_MAX_POLICY_GROUPS=6
```

---

## 5. Files touched (minimal diff estimate)

| File | Change | Lines |
|------|--------|-------|
| `services/policy_discovery.py` | adaptive cap, type fallback, category sweep, merge | +120 |
| `graph/discovery_nodes.py` | pass sections, reviewable count | +15 |
| `config.py` | 7 new settings | +15 |
| `contract_routing.py` | keyword patterns | +12 |
| `prompts/routing_topic_hints.yaml` | ESG/security topics | +15 |
| `.env.example` | document settings | +10 |
| `tests/test_policy_discovery.py` | 5 new tests | +120 |

**Not touched:** `multi_retrieval.py`, compare LLM, graph topology, P2-G grouping logic.

---

## 6. Tests (must pass)

| Test | Setup | Assert |
|------|-------|--------|
| `test_adaptive_cap_scales_with_sections` | 20 sections metadata, 15 categories | cap=15, not 6 |
| `test_adaptive_cap_cisco_floor` | 6 sections | cap=6 |
| `test_contract_type_fallback_niche` | policies `applies_to=[msa]`, contract `oem`, sparse first pass | ≥4 groups after fallback |
| `test_category_sweep_adds_minerals` | section title "Responsible Minerals", no routing topic | minerals doc in discovered |
| `test_topic_adaptive_cap` | 18 routing topics | search called 18 times (not 8) |
| `test_p2g_regression_six_liability_one` | 3 liability docs, cap=6 | still 1 liability group |

**Regression:** existing P2-G tests unchanged with `discovery_group_cap_mode=fixed`.

---

## 7. Verification (E2E)

| Run | Before | Target after P1 |
|-----|--------|-----------------|
| Scale MSA (#1) | 6 discovered, 70% coverage | **≥12 discovered, ≥85% coverage** |
| Prof services (#4) | 1 discovered, 5% coverage | **≥8 discovered, ≥60% coverage** |
| Cisco beta | 5–6 discovered, 8.3/10 | **6 discovered, ≥8.3/10** (no regression) |
| Dev UI 17-policy sync | 17 flat | **≤17, ≥10 groups** (deduped) |

**Command:**

```powershell
cd Legal\temp_java_sync
python beta_test\run_scale_benchmark.py
python beta_test\run_cisco_assessment.py
```

---

## 8. Rollout / risk

| Risk | Mitigation |
|------|------------|
| Too many policies in scope → 429 / slow compare | Ceiling=20; P2-G dedupe; existing compare batching |
| Cisco regression (too many groups) | `discovery_min_policy_groups=6` floor only raises cap; 6-section → cap stays 6 |
| Category sweep adds wrong family | One winner per group; retrieval reranker still filters per section |
| contract_type fallback pulls irrelevant policies | Group cap + section category hard-filter at retrieval |

---

## 9. Implementation checklist

- [x] **P1-1** `resolve_discovery_group_cap()` + config
- [x] **P1-2** Adaptive topic cap
- [x] **P1-3** Contract-type search fallback
- [x] **P1-4** Section-lexical category sweep + wire sections in discovery_node
- [x] **P1-5** Routing keywords + hints YAML
- [x] **P1-6** Extended discovery_meta
- [x] **P1-7** Unit tests (6 above)
- [ ] **P1-8** Scale + Cisco E2E re-run

---

## 10. What this plan does NOT fix (out of scope)

| Issue | Plan |
|-------|------|
| Classifier batch fail → `general` | Phase 22 P2 (lexical patterns) |
| INSUFFICIENT_POLICY_CONTEXT semantics | Phase 22 P3 (silence trust) |
| Wrong-policy compare when scope > 1 | Phase 22 P4 (category-aligned compare) |
| `policy_quote` list schema drift | Phase 22 P4 |

**P1 alone** targets **discovery scope** — the largest single contributor to ~50% accuracy on enterprise contracts.

---

*End of Phase 22 P1 plan — adaptive discovery scope for 40+ playbook tenants.*
