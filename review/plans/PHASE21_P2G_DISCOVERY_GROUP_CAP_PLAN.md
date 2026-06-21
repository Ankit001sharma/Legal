# Phase 21 P2-G — Discovery Cap + 5–6 Grouped Policies

**Plan ID:** `DR-PHASE-21-P2G-DISCOVERY-GROUP-CAP`  
**Priority:** P2-G (retrieval scope — reduces noise before section compare)  
**Impact:** **−0 LLM calls** at discovery; **−10 to −30%** downstream compare/guard load when tenant has duplicate-category playbooks  
**Accuracy:** ★★★★★ — Cisco/Dev UI path discovers **~6 playbook families** instead of **17 redundant docs**; section retrieval scope stays aligned with contract sections  
**Depends on:** Phase 6 discovery, Phase 10 taxonomy `categories[]`, Phase 12 P3 scope filter  
**Scope:** `policy_discovery.py`, `discovered_policy.py`, `config.py`, `discovery_nodes.py`, tests  
**Non-goals:** Java sync merge UI, new graph nodes, LLM policy selection, deleting tenant index docs

---

## 0. Problem (root cause — verified in code + runs)

### Symptom (Dev UI / large tenant)

| Run | Policies in scope | Sections | Result |
|-----|-------------------|----------|--------|
| Cisco beta (10/10) | **6** grouped playbooks | 6 | Clean retrieval + compare |
| Dev UI paste (17 policies synced) | **15–17** discovered | 11 | 429, weak matching, noisy scope |

**Not a compare bug first** — discovery returns **too many document IDs** into review scope.

### Current discovery (`policy_discovery.py`)

```text
for topic in routing.topics:          # up to 15 topics
    search_policy(top_k=5)            # up to 5 hits/topic
aggregate by document_id
sort by match_score
capped = ranked[:discovery_max_policies]   # default cap = 50
```

| Gap | Effect |
|-----|--------|
| **Flat cap at 50** | Does not collapse 5× liability + 3× HR variants → still 17 docs in scope |
| **No category grouping** | Multiple indexed playbooks with same `metadata.categories` all pass through |
| **Topic explosion** | `_topics_from_section_titles` returns up to **15** topics → redundant searches |
| **Score-only ranking** | Best liability fragment wins, but **second** liability doc still in top-50 |

### Why 5–6 grouped policies is the target

Cisco supplier review maps **1 playbook family per contract section family**:

| § | Contract topic | Policy group (category) |
|---|----------------|-------------------------|
| 1 | Code of conduct | `compliance` |
| 2 | Human rights / labor | `human_rights` (+ `labor` same doc) |
| 3 | Responsible minerals | `minerals` |
| 4 | Environment / GHG | `environment` |
| 5 | Security MSS | `security` / `vendor_security` |
| 6 | Risk / BCP | `compliance` or dedicated |

**6 fixtures → 6 groups → 6 scoped document IDs** = optimal scope for section-first retrieval.

Dev UI syncs **17 separate JSON rows** (fragments) → discovery must **dedupe by taxonomy group**, not return all 17.

### What P3.3 already fixed vs this plan

| P3.3 (done) | P2-G (this plan) |
|-------------|------------------|
| Warn when flat `discovery_max_policies` truncates | **Group-first** selection before flat cap |
| Default cap 50 | Default **6 groups** (+ optional flat safety cap) |
| Stats: `discovery_capped` | Stats: `discovery_groups`, `discovery_deduped` |

---

## 1. Design principles

1. **Group by taxonomy, not title** — use ingest `metadata.categories` (same tags as section classifier + retrieval filter).
2. **One winner per group** — highest `match_score` document per group key (accuracy: best match represents family).
3. **Default 6 groups** — matches Cisco/supplier ESG + commercial supplier reviews; configurable.
4. **Fail open** — docs without categories fall back to **per-document group** (no accidental merge).
5. **Minimal diff** — extend `discover_policies_from_topics()`; no new graph node.
6. **Explicit policies untouched** — when user passes `policy_document_ids` / inline `policy_texts`, skip auto-discovery (existing `_explicit_policies_in_request`).
7. **0 LLM** — deterministic grouping only.

---

## 2. Target flow (after P2-G)

```text
contract_routing → topics[] (cap to discovery_max_topics)
        │
        ▼
policy_discovery_node
        │
        ├─ per-topic search (unchanged)
        ├─ aggregate by document_id (unchanged)
        ├─ NEW: assign group_key per policy
        ├─ NEW: keep best match_score per group_key
        ├─ sort groups by score → take top discovery_max_policy_groups (default 6)
        ├─ optional: flat cap discovery_max_policies (safety, default 0=off)
        └─ set policy_document_ids + discovered_policy_document_ids
                │
                ▼
section_policy_retrieval (scope_document_ids = 6, not 17)
```

**Downstream unchanged:** per-section retrieval still runs dense+FTS+metadata inside scope; compare still batched.

---

## 3. Group key algorithm (accuracy-critical)

**File:** `policy_discovery.py` — new helper `_policy_group_key()` (~25 lines)

```python
def _policy_group_key(
    *,
    categories: list[str],
    metadata: dict,
    matched_topics: list[str],
    document_id: str,
) -> str:
    # 1. Explicit ingest group (optional, Java/sync)
    if g := (metadata.get("policy_group") or metadata.get("playbook_group") or "").strip():
        return g.lower()

    # 2. Primary taxonomy category (canonical)
    if categories:
        return categories[0]

    # 3. First matched discovery topic (stable phrase)
    if matched_topics:
        return matched_topics[0].lower().replace(" ", "_")[:64]

    # 4. No merge — one doc per id
    return f"doc:{document_id}"
```

**Extract categories from hit:**

```python
raw = parent.metadata.get("categories") or []
categories = normalize_categories(raw if isinstance(raw, list) else [])
```

Also read from `IngestRequest.categories` copied into document-level metadata at index time (already on parent chunks in pgvector store).

### Group selection

```python
def _select_grouped_policies(
    ranked: list[DiscoveredPolicy],
    *,
    max_groups: int,
    max_policies: int,
) -> tuple[list[DiscoveredPolicy], int]:
    """One best policy per group_key; then cap groups."""
    best_by_group: dict[str, DiscoveredPolicy] = {}
    for policy in ranked:  # already sorted by match_score desc
        key = policy.policy_group or policy.document_id
        if key not in best_by_group:
            best_by_group[key] = policy

    grouped = sorted(best_by_group.values(), key=lambda p: p.match_score, reverse=True)
    deduped_count = len(ranked) - len(grouped)

    if max_groups > 0:
        grouped = grouped[:max_groups]
    if max_policies > 0:
        grouped = grouped[:max_policies]
    return grouped, deduped_count
```

**Acceptance:**

- [ ] 3 liability docs + 2 HR docs → **2** policies returned (best each group)
- [ ] Cisco 6 fixtures indexed → discovery returns **6** (one per category family)
- [ ] Doc with no categories → own group (never dropped unless low score / over group cap)
- [ ] `human_rights` + `labor` on **same** doc → single group key = `human_rights` (first category)

---

## 4. Config

**File:** `review_agent/config.py` (+6 lines)

```python
discovery_group_mode: Literal["category", "flat"] = "category"
discovery_max_policy_groups: int = 6
discovery_max_topics: int = 8
# discovery_max_policies: int = 50  # keep; 0 = no flat cap after grouping
```

| Env | Default | Purpose |
|-----|---------|---------|
| `DISCOVERY_GROUP_MODE` | `category` | `flat` = legacy score-only cap (A/B) |
| `DISCOVERY_MAX_POLICY_GROUPS` | `6` | Primary cap — **5–6 grouped playbooks** |
| `DISCOVERY_MAX_TOPICS` | `8` | Limit routing topics fed to search loop |
| `DISCOVERY_MAX_POLICIES` | `50` | Secondary flat cap after grouping; `0` = disabled |
| `DISCOVERY_WARN_ON_CAP` | `true` | Warn on group or flat cap (extend messages) |

**`.env.example`:**

```env
DISCOVERY_GROUP_MODE=category
DISCOVERY_MAX_POLICY_GROUPS=6
DISCOVERY_MAX_TOPICS=8
DISCOVERY_MAX_POLICIES=0
```

Set `DISCOVERY_MAX_POLICIES=0` in prod when group cap is sufficient (Cisco/Dev UI).

---

## 5. Schema extension

**File:** `schemas/discovered_policy.py` (+3 fields)

```python
class DiscoveredPolicy(BaseModel):
    document_id: str
    title: str = ""
    policy_type: str | None = None
    match_score: float = 0.0
    matched_topics: list[str] = Field(default_factory=list)
    applies_to_contract_types: list[str] = Field(default_factory=list)
    policy_group: str = ""           # group key used for dedupe
    categories: list[str] = Field(default_factory=list)
```

**`discovered_to_indexed_entries`** — pass through `policy_group`, `categories` for artifact/debug.

---

## 6. Core implementation

**File:** `policy_discovery.py` (~55 lines added, ~10 changed)

### 6.1 Topic cap (before search loop)

```python
def _cap_topics(topics: list[str], *, max_topics: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        key = topic.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(topic.strip())
        if len(out) >= max_topics:
            break
    return out
```

### 6.2 Wire grouping in `discover_policies_from_topics`

After building `ranked` list (existing):

```python
if settings.discovery_group_mode == "category":
    for policy in ranked:
        # policy_group + categories set during aggregation (see 6.3)
        pass
    capped, deduped = _select_grouped_policies(
        ranked,
        max_groups=settings.discovery_max_policy_groups,
        max_policies=settings.discovery_max_policies,
    )
else:
    capped = ranked[:settings.discovery_max_policies] if settings.discovery_max_policies > 0 else ranked
    deduped = 0
```

### 6.3 Set group fields during aggregation

When creating/updating `DiscoveredPolicy`, compute:

```python
categories = normalize_categories(
    parent.metadata.get("categories") if isinstance(parent.metadata.get("categories"), list) else []
)
group_key = _policy_group_key(
    categories=categories,
    metadata=parent.metadata or {},
    matched_topics=[topic_clean],
    document_id=doc_id,
)
```

On merge of existing entry, union `categories` and recompute group from merged categories (prefer first category).

### 6.4 Warnings + meta

```python
if settings.discovery_warn_on_cap:
    if deduped > 0:
        warnings.append(
            f"Policy discovery grouped {len(ranked)} candidates into "
            f"{len(capped)} playbook families ({deduped} duplicate-category doc(s) omitted)."
        )
    if settings.discovery_group_mode == "category" and len(best_by_group) > len(capped):
        warnings.append(
            f"Policy discovery group cap at {settings.discovery_max_policy_groups}; "
            f"{len(best_by_group) - len(capped)} group(s) omitted."
        )
```

```python
discovery_meta = {
    "discovery_total_ranked": len(ranked),
    "discovery_returned": len(capped),
    "discovery_capped": len(ranked) > len(capped),
    "discovery_groups": len(capped),
    "discovery_deduped": deduped,
    "discovery_group_mode": settings.discovery_group_mode,
}
```

**File:** `discovery_nodes.py` — no logic change; meta flows via existing `compliance_stats` merge.

---

## 7. Code to remove / simplify

| Item | Action |
|------|--------|
| Duplicate cap-only path | **Replace** with group-first + optional flat cap |
| `discovery_max_policies=50` as primary control | **Demote** — document as safety net; default flat cap **0** in `.env.example` for supplier reviews |
| New graph node `policy_group_node` | **Do not add** |
| Merge logic in `multi_retrieval` | **Out of scope** — scope already uses `policy_document_ids` from discovery |
| Java sync combining 17 JSONs | **Out of scope** — Python grouping fixes scope at review time |

---

## 8. Tests

### 8.1 `tests/test_policy_discovery.py` (extend)

| Test | Setup | Assert |
|------|-------|--------|
| `test_discovery_groups_by_category` | Index 3 policies, all `categories=["liability"]`, different titles | Returns **1** doc, `discovery_deduped >= 2` |
| `test_discovery_returns_six_cisco_groups` | Index 6 fixtures with distinct categories | Returns **6**, each unique `policy_group` |
| `test_discovery_group_cap_six` | Index 8 policies, 8 categories | Returns **6**, warning mentions group cap |
| `test_discovery_flat_mode_legacy` | `discovery_group_mode=flat`, cap=2 | Returns 2 by score (regression) |
| `test_discovery_topics_capped` | 12 routing topics, `discovery_max_topics=8` | Mock/search called ≤8 times |
| `test_discover_policies_cap_emits_warning` | **Keep** — adapt for group warning text |

Use `categories` on `IngestRequest` in tests (already supported).

### 8.2 Unit tests for helpers (optional, same file)

- `_policy_group_key` with categories / policy_group / fallback
- `_select_grouped_policies` ordering

### 8.3 Regression

```powershell
cd Legal\review\review_agent
python -m pytest tests/test_policy_discovery.py tests/test_section_retrieval_warnings.py tests/test_review_e2e.py -q
python -m pytest tests/ -q --ignore=tests/test_review_e2e.py
```

---

## 9. E2E verification

### 9.1 Dev UI (17 policies pasted)

1. Sync 17 policy blocks + 11-section contract (custom review).
2. **Before P2-G:** `discovered_policies` ≈ 15–17.
3. **After P2-G:** `discovered_policies` ≈ **5–7** (group cap 6 ± overlap).
4. Warnings include `grouped ... duplicate-category`.
5. Fewer 429 / fewer `INSUFFICIENT_POLICY_CONTEXT` vs prior run.

### 9.2 Cisco assessment

```powershell
cd Legal\temp_java_sync
python beta_test/run_cisco_assessment.py
```

| Check | Before | After |
|-------|--------|-------|
| `discovered_policies` | 6 (already grouped fixtures) | **6** (no regression) |
| Legal score | 10/10 | **≥ 10/10** |
| §2–§6 policy hits | present | **unchanged or better** |
| LLM calls | baseline | **same or lower** |

### 9.3 Artifact spot check

```json
"discovery": {
  "discovered_policy_document_ids": ["...", "..."],  // length ≤ 6
  "indexed_policies": [{ "policy_group": "human_rights", "categories": ["human_rights", "labor"] }]
}
```

---

## 10. LLM call accounting

| Stage | Impact |
|-------|--------|
| Discovery | **0 LLM** (unchanged) |
| Section retrieval | **0 LLM** — smaller scope → slightly faster MCP |
| Section compare | **−0 to 4 calls** — fewer wrong-policy compares / less noise |
| Final-verify / guard | **−0 to 3 calls** (indirect) |

**Net:** accuracy win + less rate-limit pressure; **no new LLM steps**.

---

## 11. Risk matrix

| Risk | Mitigation |
|------|------------|
| Over-merge unlike policies sharing `compliance` | Use **first category only**; optional future `policy_group` in Java sync |
| Under-discovery (cap 6 drops rare 7th family) | Raise `DISCOVERY_MAX_POLICY_GROUPS`; warn in artifact |
| Policies without categories not grouped | Each gets unique `doc:{id}` key — safe, may exceed 6 |
| Explicit `policy_document_ids` bypass | Unchanged — user intent preserved |
| Cisco HR doc has `human_rights`+`labor` | Single group — correct (one playbook) |

---

## 12. Implementation checklist

- [x] **P2-G.1** `_policy_group_key()` + category extract from parent metadata
- [x] **P2-G.2** `_select_grouped_policies()` + `_cap_topics()`
- [x] **P2-G.3** Wire grouping in `discover_policies_from_topics()` with `discovery_group_mode`
- [x] **P2-G.4** Extend `DiscoveredPolicy` + `discovered_to_indexed_entries()`
- [x] **P2-G.5** Config + `.env.example` (`discovery_max_policy_groups=6`, etc.)
- [x] **P2-G.6** Warnings + `discovery_meta` fields
- [x] **P2-G.7** Unit/integration tests (group, cap, flat regression, topic cap)
- [ ] **P2-G.8** Dev UI 17-policy + Cisco re-run verification

---

## 13. Phase 21 sequence

```text
P2 lexical-first classifier ✅ → P2-R cross-encoder reranker ✅
  → **P2-G discovery group cap (this)** → future: Java sync `policy_group` field
```

**Orthogonal to** classifier, reranker, quote grounding — safe to ship independently.

---

## 14. Before / after diagram

```text
BEFORE (Dev UI, 17 synced policies)
───────────────────────────────────
topics: 11 section titles + keywords
discovery: 17 document_ids in scope
section §3 retrieval: searches 17 playbooks → wrong/minerals fragment noise
compare: overloaded context → 429 / INCONCLUSIVE

AFTER (P2-G, group cap 6)
─────────────────────────
topics: capped to 8
discovery: 6 document_ids (compliance, human_rights, minerals, environment, security, vendor_security)
section §3 retrieval: scope = 6 → minerals playbook top-1
compare: focused → same or better violations, fewer API failures
```

---

## 15. Files touched (minimal)

| File | Change | Est. lines |
|------|--------|------------|
| `services/policy_discovery.py` | Group key, select, topic cap | +70 |
| `schemas/discovered_policy.py` | `policy_group`, `categories` | +4 |
| `config.py` | 3 new settings | +6 |
| `.env.example` | Document vars | +4 |
| `tests/test_policy_discovery.py` | Group/cap/topic tests | +80 |
| `discovery_nodes.py` | **No change** (meta passthrough) | 0 |

**Total:** ~165 lines. **No new modules.**

---

*End of Phase 21 P2-G plan — discovery cap + 5–6 grouped policies.*
