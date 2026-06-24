# Sprint 1 — Prod Gates & Health (P1.1–P1.4)

**Plan ID:** `DR-PHASE-11-P1`  
**Scope:** Review agent + platform gateway/orchestrator/session only  
**Goal:** Fail fast before LLM spend; prod reviews use pre-synced `contract_document_id` and tenant policy index — no silent half-reviews, no re-ingest drift.  
**Depends on:** Phase 10D (`contract_document_id` path, `validate_review_inputs`)  
**Estimate:** ~180 lines prod code, ~140 lines tests, **1–2 days**  
**Out of scope:** Java sync worker, platform startup health aggregation, retrieval-mcp preflight, `sections[]` ingest

---

## 0. Problem statement (verified in code)

| ID | Requirement | Current state | Risk |
|----|-------------|---------------|------|
| P1.1 | `REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID=true` → reject `contract_text`-only | **Partial.** `validate_review_inputs()` in `review_graph.py` raises when flag set and no doc id — but orchestrator still accepts text-only and only fails inside `ReviewAgent.execute()` | Bad HTTP semantics; Java clients get 200 with `success=false` instead of 400 |
| P1.2 | Reject/warn inline `policy_texts` in prod | **Missing.** `policies[]` / `policy_texts` always ingested in `index_policies_node` (`nodes.py` L148+); discovery skipped when inline policies present (`discovery_nodes.py` L20) | Tenant index bypass; parser/embedding drift per review |
| P1.3 | Preflight: LLM key, document-mcp `/health`, Postgres | **Missing.** `run_review()` jumps straight to `graph.ainvoke()`. LLM key checked lazily on first LLM call (`llm_gateway.py`). document-mcp has `GET /health` with `store.ping()` (`document_server/main.py` L125–140); `DocumentMCPClient.health()` exists but is **never called** before review | Minutes of partial graph + spend before hard fail |
| P1.4 | `contract_document_id` on gateway/matter/context | **Partial.** `run_review` + platform `ReviewAgent` read `context["contract_document_id"]`. `AgentRequest` has **no** top-level field; `MatterSnapshot` has **no** `contract_document_id`; orchestrator `_validate_review_payload` does not read `request.contract_document_id` | Java must nest in `context`; multi-turn session loses doc id |

---

## 1. Design principles

1. **Single validation function** — extend `validate_review_inputs()`; orchestrator calls a thin shared helper or duplicates the same rules (prefer import from review package in platform tests only; platform orchestrator mirrors rules to avoid cross-package import — see §4).
2. **Fail before graph** — preflight + input gates run in `run_review()` before `build_review_graph().ainvoke()`.
3. **Fail before agent dispatch** — orchestrator validates review payload for gateway 400 responses.
4. **Dev path preserved** — all flags default `false`; local `contract_text` + inline policies still work.
5. **Minimal surface** — no new graph nodes; one new service file (`review_preflight.py`); no direct Postgres client in review agent (trust document-mcp `db` field).

---

## 2. Environment variables

| Variable | Default | Where read | Purpose |
|----------|---------|------------|---------|
| `REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID` | `false` | `ReviewSettings` (exists) | P1.1 — reject text-only |
| `REVIEW_REJECT_INLINE_POLICIES` | `false` | `ReviewSettings` (**new**) | P1.2 — reject non-empty `policy_texts` / `policies[]` with inline `text` |
| `REVIEW_PREFLIGHT_ENABLED` | `true` | `ReviewSettings` (**new**) | P1.3 — skip preflight only in unit tests |
| `REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID` | `false` | Platform `PlatformSettings` or `os.environ` (**new**, mirror) | P1.4 — orchestrator early 400 |

Add to:

- `Legal/review/review_agent/.env.example`
- `Legal/legal_ai_platform/.env.example` (platform mirror for orchestrator)

**Prod profile (document in README / deploy checklist, not code):**

```env
REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID=true
REVIEW_REJECT_INLINE_POLICIES=true
REVIEW_PREFLIGHT_ENABLED=true
```

---

## 3. Task breakdown

### P1.1 — Require `contract_document_id` in prod

#### 3.1.1 Review agent (enforce)

**File:** `review_agent/graph/review_inputs.py`

Extend signature:

```python
def validate_review_inputs(
    *,
    contract_text: str,
    contract_document_id: str | None,
    require_contract_document_id: bool = False,
    policy_texts: list[dict] | None = None,          # P1.2
    reject_inline_policies: bool = False,              # P1.2
) -> tuple[str | None, list[str]]:
```

**P1.1 logic (clarify existing):**

```python
if require_contract_document_id:
    if not doc_id_raw:
        raise ValueError(
            "contract_document_id is required when REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID=true"
        )
    # Optional hardening: reject stray contract_text without doc_id already covered above.
```

No change to “both sent” behavior: keep warning `"contract_text ignored when contract_document_id is set"`.

**File:** `review_agent/graph/review_graph.py` — pass settings flags into `validate_review_inputs()` before graph build (already calls it; add P1.2 args).

#### 3.1.2 Platform orchestrator (fail fast, 400)

**File:** `legal_ai_platform/orchestration/orchestrator.py`

Extend `_validate_review_payload`:

```python
contract_document_id = (
    context.get("contract_document_id")
    or getattr(request, "contract_document_id", None)  # after P1.4 field added
    or matter.get("contract_document_id")
    or ""
).strip()

if _require_contract_document_id():  # platform env mirror
    if not contract_document_id:
        raise ReviewPayloadError(
            "Review requires contract_document_id when REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID=true"
        )
```

**Minimal platform config:** add to `legal_ai_platform/config.py` (or existing settings module):

```python
review_require_contract_document_id: bool = False  # env: REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID
```

Same env var name as review agent — both processes read identical value in prod.

#### 3.1.3 Acceptance (P1.1)

- [ ] `REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID=false`: `contract_text` only → review runs (dev).
- [ ] `REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID=true`: `contract_text` only → `ValueError` in agent, `ReviewPayloadError` → HTTP 400 at gateway.
- [ ] `REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID=true`: valid UUID doc id → review runs; `contract_text` ignored with warning.

---

### P1.2 — Reject inline `policy_texts` in prod

#### 3.2.1 Semantics

| Input | Prod (`REVIEW_REJECT_INLINE_POLICIES=true`) | Dev |
|-------|---------------------------------------------|-----|
| `policies[]` with non-empty `text` | **Reject** | Allow (current) |
| `policy_document_ids[]` | Allow (tenant index) | Allow |
| `policy_refs[]` | Allow (catalog → tenant sync path) | Allow |
| Empty policies + tenant_auto discovery | Allow (default prod path) | Allow |

**Reject condition:**

```python
def _has_inline_policy_texts(policy_texts: list[dict] | None) -> bool:
    for p in policy_texts or []:
        if (p.get("text") or "").strip():
            return True
    return False
```

**Error message (stable for Java clients):**

```text
Inline policy text is not allowed when REVIEW_REJECT_INLINE_POLICIES=true; sync policies to document-mcp and use policy_document_ids or policy_refs
```

Implement in `validate_review_inputs()` — keeps one front door for `run_review()`.

#### 3.2.2 Orchestrator mirror

In `_validate_review_payload`, after resolving policies from `request.policies` / `context["policies"]` / `matter.policies`:

```python
if _reject_inline_policies() and _has_inline_policy_texts(policies):
    raise ReviewPayloadError(...)
```

Do **not** reject `policy_document_ids` / `policy_refs` — those are the Java-ready path.

#### 3.2.3 Optional warn mode (defer)

Sprint says “reject / warn”. **v1: reject only** via bool flag. If staging needs warn-without-fail, add `REVIEW_WARN_INLINE_POLICIES` later (append to `input_warnings`, strip inline texts) — **not in Sprint 1** to keep diff minimal.

#### 3.2.4 Acceptance (P1.2)

- [ ] Prod flag + `policies: [{title, text}]` → 400 at gateway, no graph invoke.
- [ ] Prod flag + `policy_refs: ["ref-1"]` only → passes validation (discovery/index_policies handles refs).
- [ ] Prod flag + no policies → tenant_auto discovery runs.

---

### P1.3 — Fail-fast preflight before graph

#### 3.3.1 New module

**File:** `review_agent/services/review_preflight.py` (~55–70 lines)

```python
class ReviewPreflightError(RuntimeError):
    """Review cannot start — dependency unavailable."""

async def run_review_preflight(
    client: DocumentMCPClient,
    *,
    preflight_enabled: bool = True,
) -> None:
    if not preflight_enabled:
        return
    _check_llm_credentials()
    await _check_document_mcp(client)

def _check_llm_credentials() -> None:
    # Mirror llm_gateway.py: need api_key OR on-prem base_url without key requirement
    ...

async def _check_document_mcp(client: DocumentMCPClient) -> None:
    data = await client.health()
    if data.get("status") not in ("ok",):          # degraded = fail
        raise ReviewPreflightError(f"document-mcp unhealthy: {data}")
    if data.get("db") != "ok":
        raise ReviewPreflightError("document-mcp Postgres ping failed")
```

**LLM credential rules (match production reality):**

| Condition | Pass |
|-----------|------|
| `LLM_API_KEY` or `OPENAI_API_KEY` or `MISTRAL_API_KEY` set | Yes |
| `LLM_BASE_URL` set (on-prem, key optional) | Yes |
| Neither | **Fail** with `ReviewPreflightError("LLM credentials not configured")` |

Do **not** instantiate `init_chat_model` in preflight — too slow; env check only.

**Postgres:** no second ping in review agent. document-mcp `/health` already calls `store.ping()` (`pgvector_store.py` L83). Treat `db != "ok"` as hard fail.

#### 3.3.2 Wire into `run_review()`

**File:** `review_agent/graph/review_graph.py`

```python
from review_agent.services.review_preflight import ReviewPreflightError, run_review_preflight

async def run_review(...):
    get_settings.cache_clear()
    settings = get_settings()
    parsed_doc_id, input_warnings = validate_review_inputs(...)
    await run_review_preflight(
        client,
        preflight_enabled=settings.review_preflight_enabled,
    )
    graph = build_review_graph(...)
    ...
```

Order: **validate inputs → preflight → graph**.

#### 3.3.3 Platform error mapping

**File:** `legal_ai_platform/agents/review/review_agent.py`

Map preflight distinctly (optional, 5 lines):

```python
except ReviewPreflightError as exc:
    return AgentResponse(..., error=f"preflight failed: {exc}", success=False)
```

Gateway already returns agent errors in 200 body; orchestrator 400 is only for `ReviewPayloadError`. Preflight failures are **503-style semantically** but keep current envelope (`success=false`, clear `error` prefix) unless you add `HTTPException` mapping later — **Sprint 1: agent error string is enough**.

#### 3.3.4 Tests (no Postgres)

**File:** `review_agent/tests/test_review_preflight.py`

| Test | Mock |
|------|------|
| missing LLM key | patch env, expect `ReviewPreflightError` |
| document-mcp `db=error` | mock `client.health()` |
| preflight disabled | `REVIEW_PREFLIGHT_ENABLED=false`, no health call |
| happy path | mock health `{status: ok, db: ok}` |

#### 3.3.5 Acceptance (P1.3)

- [ ] Stopped document-mcp → review fails in &lt;2s with `preflight failed`, zero LLM calls.
- [ ] Empty `LLM_API_KEY` + no base URL → same.
- [ ] Healthy stack → no added latency beyond one HTTP GET.

---

### P1.4 — Gateway / session `contract_document_id`

#### 3.4.1 `AgentRequest` top-level field

**File:** `legal_ai_platform/models/agent.py`

```python
contract_document_id: str | None = Field(
    default=None,
    description="Pre-synced contract UUID in document-mcp (prod path)",
)
```

**`effective_context()` merge:**

```python
if self.contract_document_id is not None:
    merged["contract_document_id"] = self.contract_document_id
```

#### 3.4.2 Session matter persistence

**File:** `legal_ai_platform/session/models.py`

```python
class MatterSnapshot(BaseModel):
    contract_text: str | None = None
    contract_document_id: str | None = None   # NEW
    ...
```

**File:** `legal_ai_platform/session/service.py`

`capture_matter_from_request`:

```python
doc_id = request.contract_document_id or ctx.get("contract_document_id")
if doc_id:
    state.matter.contract_document_id = str(doc_id).strip()
```

`merge_matter_into_request`:

```python
if not request.contract_document_id and matter.contract_document_id:
    updates["contract_document_id"] = matter.contract_document_id
```

Also merge into context for agents that only read `effective_context()` — `effective_context()` already handles top-level after model_copy.

#### 3.4.3 Orchestrator resolution order

**File:** `orchestrator.py` `_validate_review_payload` and (implicitly) enriched request

Resolution order (document in code comment):

```text
request.contract_document_id
→ context.contract_document_id
→ session.matter.contract_document_id
```

#### 3.4.4 Platform `ReviewAgent`

**File:** `agents/review/review_agent.py`

Prefer top-level field:

```python
contract_document_id = (
    request.contract_document_id
    or context.get("contract_document_id")
)
```

#### 3.4.5 Java-ready API example

```json
POST /query
{
  "task_type": "review",
  "tenant_id": "acme",
  "thread_id": "matter-123",
  "contract_document_id": "550e8400-e29b-41d4-a716-446655440000",
  "contract_title": "MSA 2026"
}
```

No `contract_text`, no `policies` — discovery + tenant index only.

#### 3.4.6 Acceptance (P1.4)

- [ ] Top-level `contract_document_id` reaches `run_review(contract_document_id=...)`.
- [ ] Turn 2: omit doc id; session matter backfills via `merge_matter_into_request`.
- [ ] `capture_matter_from_request` persists doc id to Postgres session JSONB.

---

## 4. File change matrix

| File | Action | Tasks | ~Lines |
|------|--------|-------|--------|
| `review_agent/config.py` | Modify | P1.2, P1.3 flags | +4 |
| `review_agent/graph/review_inputs.py` | Modify | P1.1 clarify, P1.2 | +25 |
| `review_agent/services/review_preflight.py` | **Create** | P1.3 | +60 |
| `review_agent/graph/review_graph.py` | Modify | P1.2 args, P1.3 call | +8 |
| `legal_ai_platform/models/agent.py` | Modify | P1.4 | +8 |
| `legal_ai_platform/session/models.py` | Modify | P1.4 | +1 |
| `legal_ai_platform/session/service.py` | Modify | P1.4 | +12 |
| `legal_ai_platform/orchestration/orchestrator.py` | Modify | P1.1, P1.2, P1.4 | +35 |
| `legal_ai_platform/config.py` (or settings) | Modify | P1.1 platform mirror | +5 |
| `legal_ai_platform/agents/review/review_agent.py` | Modify | P1.3 error, P1.4 | +6 |
| `review_agent/.env.example` | Modify | docs | +4 |
| `legal_ai_platform/.env.example` | Modify | docs | +4 |
| `review_agent/tests/test_review_preflight.py` | **Create** | P1.3 | +70 |
| `review_agent/tests/test_contract_by_id.py` | Modify | P1.1+P1.2 cases | +30 |
| `legal_ai_platform/tests/test_review_gateway.py` | Modify | P1.1 400, P1.4 top-level | +40 |
| `legal_ai_platform/tests/test_session_service.py` | Modify | P1.4 matter | +20 |

**Total:** ~330 lines (within 1–2 day estimate).

---

## 5. Implementation order (do in sequence)

```text
Day 1 AM
  1. review_inputs.py — P1.1 + P1.2 (single function)
  2. config.py flags + .env.example
  3. test_contract_by_id.py — gate tests (no Postgres)

Day 1 PM
  4. review_preflight.py + wire run_review
  5. test_review_preflight.py

Day 2 AM
  6. AgentRequest + MatterSnapshot + session service (P1.4)
  7. orchestrator _validate_review_payload (P1.1, P1.2, P1.4)
  8. platform settings mirror

Day 2 PM
  9. gateway + session tests
  10. Manual smoke: docker stack up, review by doc id only, flags true
```

---

## 6. Test plan

### Unit (no Postgres)

| Test file | Cases |
|-----------|-------|
| `test_contract_by_id.py` | require doc id; reject inline policies; combined flags |
| `test_review_preflight.py` | LLM missing, mcp degraded, disabled skip |
| `test_session_service.py` | capture + merge `contract_document_id` |
| `test_review_gateway.py` | 400 when prod flags + bad payload; 200 doc-id-only stub path |

### Integration (Postgres + document-mcp, local docker)

| Scenario | Expected |
|----------|----------|
| Review with `contract_document_id` only, flags off | 200, no ingest call (mock/spy `ingest_document`) |
| Same with `REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID=true` | 200 |
| `contract_text` only + require flag | 400 before agent |
| Inline policy + reject flag | 400 |

**CI note:** existing autouse `pg_document_store` fixture skips most tests without Postgres — prod gate unit tests must **not** depend on pg fixture (pure validation + mocked health).

---

## 7. Deploy / rollout

| Phase | Flags | Who |
|-------|-------|-----|
| Dev | all `false` | Engineers — text + inline policies |
| Staging | `REVIEW_PREFLIGHT_ENABLED=true` only | Catch infra issues |
| Prod | all three `true` | After Java syncs contracts + policies |

**Rollback:** set flags `false` — no migration, no graph change.

---

## 8. Explicit non-goals (Sprint 1)

- Platform gateway `/health` aggregating review preflight (already has retrieval/document checks at gateway level — separate concern).
- Rejecting `contract_text` when **both** text and doc id sent (keep warn-only).
- Automatic stripping of inline policies (warn mode).
- Shared Python package imported by platform from `review_agent` (avoid coupling; duplicate orchestrator rules).
- Tombstone / stale doc id validation (future: document-mcp `GET /tools/get_document` preflight).

---

## 9. Definition of done

Sprint 1 is complete when:

1. All four acceptance checklists (§3.1.3, §3.2.4, §3.3.5, §3.4.6) pass.
2. Prod env example documented in both `.env.example` files.
3. Java integration doc shows top-level `contract_document_id` on `POST /query`.
4. No new graph nodes; review latency increase &lt; 100ms from one health GET.
5. Existing dev tests (`contract_text` + inline policies) pass with default flags.

---

## 10. Quick reference — request flow after P1

```text
POST /query (task_type=review)
    │
    ▼
Orchestrator._validate_review_payload     ← P1.1, P1.2, P1.4 (400 ReviewPayloadError)
    │
    ▼
Session enrich + merge matter             ← P1.4 contract_document_id
    │
    ▼
ReviewAgent.execute → run_review()
    │
    ├─ validate_review_inputs()          ← P1.1, P1.2
    ├─ run_review_preflight()            ← P1.3
    └─ graph.ainvoke()                   ← existing section-first pipeline
```

---

**Next sprint (not P1):** stale doc id check, gateway 503 mapping for preflight, E2E CI job with Postgres, remove deprecated `REVIEW_POLICY_SOURCE` from any local `.env` leftovers.
