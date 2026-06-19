# Sprint 6 — Guard Pass + CI + Prod Session (P6.1–P6.3)

**Plan ID:** `DR-PHASE-15-P6`  
**Scope:** Review agent Python, test/CI infra, platform prod env examples  
**Goal:** Catch **hallucinated reasoning** after quote grounding; **stop skip-all pytest**; document **Postgres session/memory** for production.  
**Depends on:** P1 prod gates, P3 retrieval, P4 grounding downgrade, P5 audit artifact  
**Estimate:** ~280 lines prod code, ~320 lines CI/tests, **2–3 days**  
**Explicitly excluded:** YAML/regex rule engine, NLI models, new compare/judgment paths, Java sync worker

---

## 0. Corrected critical path (actual status)

Your list referenced **“P4 rule engine”** — that was **removed** in Sprint 4 revision. Current path:

| Sprint | Status | What it did |
|--------|--------|-------------|
| **P1** Prod gates + fail-fast | ✅ Done | `contract_document_id`, reject inline policies, preflight |
| **P2** `sections[]` ingest + tombstone MCP | ❌ Not started | Java sends structure; stable section boundaries |
| **P3** Structured retry + typed retrieval logs | ✅ Done | Retry ladder, category filter, `retrieval_meta.attempts[]` |
| **P4** Playbook judgment (dynamic LLM) | ✅ Done | Metadata hints, conflicts, grounding downgrade — **no rule engine** |
| **P5** Artifacts + report synthesizer | ✅ Done | `ReviewArtifact`, `metadata.artifact`, ops markdown |
| **P6** Guard pass + CI E2E | ⬅ **This sprint** | Rationale guard, Postgres CI, prod session env |

**After P6, still open (not this sprint):** P2 ingest, Java playbook metadata on sync, optional Postgres `review_runs`, NLI pre-filter (Phase 15+), bulk review UI.

---

## 1. What Sprint 6 does (plain language)

Sprint 6 closes the last **trust** and **engineering** gaps before production hardening:

1. **P6.1 Guard pass** — Today we verify quotes are **verbatim** (substring + MCP `verify_quote`). We do **not** verify the **rationale matches the quotes**. A small structured LLM check asks: *“Does this rationale follow from these quotes?”* Fail → downgrade to `INCONCLUSIVE` (same pattern as P4 grounding — never silent drop).

2. **P6.2 CI E2E** — Almost all review tests **skip** when Postgres is down (`conftest.py` autouse `pg_document_store` → `pytest.skip`). CI will run Docker Postgres + real pgvector retrieval + **mock compare LLM** so tests actually execute in every PR.

3. **P6.3 Platform prod env** — Postgres session/memory **code exists** (`SessionPostgresStore`, `PostgresMemoryStore`, `container.py` L92–110) but `.env.example` defaults to `file`/`mcp`. Add `.env.production.example` with `SESSION_STORE_BACKEND=postgres` and `MEMORY_STORE_BACKEND=postgres` — docs only, no new backend code.

**Not in P6:** deterministic rules, re-ingest per review, new graph topology beyond one optional guard step.

---

## 2. Problem statement (verified in code)

| ID | Gap | Current code |
|----|-----|--------------|
| P6.1 | Quotes verified, **rationale unchecked** | `quote_validate.py` — substring only; `grounding_node` — MCP quote match only (`nodes.py` L211–278) |
| P6.1 | LLM can write plausible rationale unrelated to quotes | Compare prompt asks for rationale; no post-grounding entailment check |
| P6.2 | Tests skip without Postgres | `review_agent/tests/conftest.py` L22–37 autouse + L57 `pytest.skip` |
| P6.2 | No CI workflow | No `.github/workflows/` in repo |
| P6.2 | E2E exists but skipped locally | `test_review_e2e.py` — good mock-LLM path; needs Postgres to run |
| P6.3 | Prod session defaults to file | `legal_ai_platform/.env.example` L8–13 — dev-safe defaults |
| P6.3 | Postgres backends implemented but undocumented for prod | `container.py` L92–110; Phase 9 plan done, prod example missing |

**Already strong (keep):**

- Quote downgrade at compare (`validate_and_normalize_quotes`)  
- Grounding downgrade not drop (P4)  
- `ReviewArtifact.ops.ungrounded_count` / `grounding_downgraded_count` (P5)  
- Platform `SessionPostgresStore` + `PostgresMemoryStore`  

---

## 3. Design principles

1. **Dynamic only** — guard uses **small LLM** on finding text; no repo rules/thresholds (aligned with P4).  
2. **Guard ≠ compare** — guard never re-judges compliance; only checks quote→rationale support.  
3. **Fail safe** — guard failure → `INCONCLUSIVE` + `metadata.guard_failed=true`; same as grounding.  
4. **Minimal graph change** — prefer **function called at end of `grounding_node`** over new node (zero topology change).  
5. **CI splits unit vs integration** — unit tests never skip; integration requires Postgres service.  
6. **Mock LLM in CI, real retrieval** — catches retrieval/regression; stable/cheap CI.

---

## 4. Target pipeline (after P6)

```text
… → final_gap_verify → grounding
                          ├─ verify_quote (existing)
                          └─ guard_pass (P6.1 — NEW, inline)
                      → report → save_memory
```

---

## 5. Task breakdown

### P6.1 — Guard pass: “quote supports rationale?”

#### 5.1.1 What guard checks (and does not)

| Checks | Does not check |
|--------|----------------|
| Rationale logically supported by `contract_quote` + `policy_quote` | Whether contract meets policy (compare LLM already did) |
| Quotes cited in rationale when status is COMPLIANT/NON_COMPLIANT | New policy requirements |
| Obvious contradiction between quotes and rationale text | Full legal entailment (NLI — later) |

**Skip guard for:** `INSUFFICIENT_POLICY_CONTEXT`, `POLICY_CONFLICT`, findings already `INCONCLUSIVE`, findings with `grounding_failed`, no quotes.

#### 5.1.2 New module

**File:** `review_agent/services/guard_pass.py` (~90 lines)

```python
class RationaleGuardResult(BaseModel):
    supported: bool
    reason: str = Field(max_length=500)

async def guard_finding(
    finding: ComplianceFinding,
    *,
    settings: ReviewSettings,
) -> ComplianceFinding:
    """Return finding unchanged, or downgraded copy with metadata.guard_failed."""

async def run_guard_pass(
    findings: list[ComplianceFinding],
    *,
    settings: ReviewSettings | None = None,
) -> tuple[list[ComplianceFinding], list[str], dict[str, int]]:
    """Batch guard; stats: guard_checked, guard_failed, guard_skipped."""
```

**LLM call:** structured output `RationaleGuardResult` via existing `invoke_structured` + `get_review_model`.

**Prompt file:** `prompts/rationale_guard.md` (~25 lines)

```text
SYSTEM: You verify whether a rationale is supported by the given quotes.
Do NOT re-judge compliance. Answer supported=true only if the rationale
follows from the quotes without inventing facts.

USER:
status: {status}
contract_quote: ```
{contract_quote}
```
policy_quote: ```
{policy_quote}
```
rationale: {rationale}
```

**Input cap:** truncate quotes to 800 chars each in prompt (already verified substrings).

#### 5.1.3 Wire — inline in `grounding_node` (minimal)

**File:** `graph/nodes.py` — after grounding loop, before post-grounding coverage:

```python
if settings.guard_pass_enabled:
    grounded, guard_warnings, guard_stats = await run_guard_pass(grounded, settings=settings)
    warnings.extend(guard_warnings)
    # merge guard_stats into compliance_stats via return dict
```

**Alternative rejected:** new `guard_node` in graph — extra edge for ~5 lines savings; inline is smaller diff.

#### 5.1.4 Config

| Variable | Default | Purpose |
|----------|---------|---------|
| `GUARD_PASS_ENABLED` | `true` | Master switch |
| `GUARD_PASS_MODE` | `llm` | Only `llm` in v1 (no `rules` — reserved, unimplemented) |
| `GUARD_PASS_CONCURRENCY` | `4` | Parallel guard calls |
| `GUARD_PASS_MIN_STATUS` | — | Guard only `COMPLIANT` + `NON_COMPLIANT` |

#### 5.1.5 Artifact + report (P5 extension)

**File:** `services/review_artifact.py` — add to `ReviewArtifactOps`:

```python
guard_checked: int = 0
guard_failed: int = 0
```

**File:** `reports/generator.py` — one ops table row + executive summary line.

**Finding metadata:**

```python
{"guard_failed": True, "prior_status": "...", "guard_reason": "..."}
```

#### 5.1.6 Acceptance (P6.1)

- [ ] Mock LLM returns `supported=false` → finding becomes `INCONCLUSIVE`, `metadata.guard_failed=true`.  
- [ ] Mock LLM returns `supported=true` → finding unchanged.  
- [ ] `INSUFFICIENT_POLICY_CONTEXT` never sent to guard LLM.  
- [ ] `GUARD_PASS_ENABLED=false` → identical to pre-P6 behavior.  
- [ ] No YAML, regex, or repo legal rules added.

---

### P6.2 — CI: Docker Postgres + pytest E2E (no skip-all)

#### 5.2.1 Root cause

```python
# review_agent/tests/conftest.py — PROBLEM
@pytest.fixture(autouse=True)
def pg_document_store(pg_engine, database_url):  # every test depends on PG

@pytest.fixture(scope="session")
def pg_engine(...):
    except Exception:
        pytest.skip(...)  # → 13+ tests skipped when PG down
```

#### 5.2.2 Fix — split fixtures (minimal)

**File:** `review_agent/tests/conftest.py`

1. **Remove `autouse=True`** from `pg_document_store`.  
2. Add marker registration:

```python
def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires Postgres pgvector")
```

3. Apply `@pytest.mark.integration` to:
   - `test_review_e2e.py` (all tests)
   - `test_multi_retrieval.py`
   - `test_final_gap_verify.py` (if uses pg store)
   - Any test using `pg_document_store` fixture

4. Add **session-scoped** fixture used only by integration tests:

```python
@pytest.fixture
def pg_document_store(pg_engine, database_url):
    ...  # same body, not autouse
```

5. **Pure unit tests** (`test_playbook_context`, `test_conflict_resolve`, `test_review_artifact`, `test_report_generator`, `test_review_preflight`) run **without Postgres** — zero skip.

#### 5.2.3 CI workflow

**File:** `.github/workflows/review-ci.yml` (~60 lines)

```yaml
name: Review CI
on: [push, pull_request]
jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e Legal/document_core -e Legal/review/review_agent ...
      - run: |
          cd Legal/review/review_agent
          pytest tests/ -m "not integration" -v

  integration:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_USER: legalai
          POSTGRES_PASSWORD: legalai
          POSTGRES_DB: legalai
        ports: ["5435:5432"]
        options: >-
          --health-cmd "pg_isready -U legalai -d legalai"
          --health-interval 5s
          --health-retries 10
    env:
      DATABASE_URL: postgresql://legalai:legalai@localhost:5435/legalai
      DOCUMENT_STORE_BACKEND: pgvector
    steps:
      - ... same setup ...
      - run: |
          cd Legal/review/review_agent
          pytest tests/ -m integration -v
```

**E2E assertion additions** (`test_review_e2e.py`):

```python
assert report.metadata.get("artifact", {}).get("artifact_version") == "1.0"
assert report.metadata.get("pipeline") == "section_first"
```

Optional third job: `platform` integration with `-m integration` for `legal_ai_platform/tests/test_session_postgres.py`.

#### 5.2.4 Local dev helper

**File:** `Legal/review/review_agent/scripts/run_integration_tests.ps1` (+ `.sh`)

```bash
# Ensure postgres on 5435 (docker compose up postgres from Legal ai/docker-compose.yml)
pytest tests/ -m integration -v
```

#### 5.2.5 Acceptance (P6.2)

- [ ] `pytest tests/ -m "not integration"` passes with **no Postgres** running.  
- [ ] `pytest tests/ -m integration` passes with Postgres on `localhost:5435`.  
- [ ] CI green on PR: unit job always runs; integration job uses service container.  
- [ ] No test silently skips in CI (fail if PG unreachable in integration job).

---

### P6.3 — Platform prod env: Postgres session + memory

#### 5.3.1 Scope — docs + example only

Backend code **already exists**. P6.3 = production env template + validate container wiring — **no new store code**.

#### 5.3.2 New file

**File:** `legal_ai_platform/.env.production.example` (~35 lines)

```env
# Production — unified platform
SESSION_STORE_BACKEND=postgres
MEMORY_STORE_BACKEND=postgres
DATABASE_URL=postgresql://legalai:legalai@postgres:5432/legalai
PLATFORM_OWNS_SESSION=true
PLATFORM_OWNS_LONG_TERM_MEMORY=true

DOCUMENT_SERVER_URL=http://document-mcp:8002
RETRIEVAL_SERVER_URL=http://retrieval-mcp:8001

# Review prod gates (mirror review_agent)
REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID=true
REVIEW_REJECT_INLINE_POLICIES=true
REVIEW_PREFLIGHT_ENABLED=true
```

#### 5.3.3 Update existing examples

| File | Change |
|------|--------|
| `legal_ai_platform/.env.example` | Comment block: “production → see `.env.production.example`” |
| `review_agent/.env.production.example` | Add P4/P5/P6 flags: `GUARD_PASS_ENABLED`, artifact flags |
| `Legal ai/docker-compose.yml` | Ensure platform service (if present) gets `SESSION_STORE_BACKEND=postgres` — verify only |

#### 5.3.4 Startup validation (optional, ~15 lines)

**File:** `legal_ai_platform/container.py` — in `_build_session_store`:

```python
if self.settings.session_store_backend == "postgres":
    self._require_database_url()  # already raises
```

Add log line at container init when prod backends selected (observability only).

#### 5.3.5 Acceptance (P6.3)

- [ ] `.env.production.example` documents postgres session + memory.  
- [ ] `container.py` starts with `SESSION_STORE_BACKEND=postgres` + valid `DATABASE_URL` (manual smoke).  
- [ ] Dev `.env.example` unchanged defaults (`file` / `mcp`).

---

## 6. File change matrix

| File | Action | Task | ~Lines |
|------|--------|------|--------|
| `services/guard_pass.py` | **Create** | P6.1 | 90 |
| `prompts/rationale_guard.md` | **Create** | P6.1 | 25 |
| `graph/nodes.py` | Modify | P6.1 inline guard | 20 |
| `config.py` | Modify | guard flags | 8 |
| `schemas/review_artifact.py` | Modify | guard ops fields | 4 |
| `services/review_artifact.py` | Modify | guard ops derive | 10 |
| `reports/generator.py` | Modify | guard ops row | 4 |
| `review_agent/.env.example` | Modify | guard docs | 6 |
| `tests/conftest.py` | Modify | split autouse | 25 |
| `tests/test_guard_pass.py` | **Create** | P6.1 | 80 |
| `tests/test_review_e2e.py` | Modify | artifact assert | 8 |
| `.github/workflows/review-ci.yml` | **Create** | P6.2 | 65 |
| `scripts/run_integration_tests.sh` | **Create** | P6.2 | 12 |
| `legal_ai_platform/.env.production.example` | **Create** | P6.3 | 35 |
| `legal_ai_platform/.env.example` | Modify | prod pointer | 4 |
| `review_agent/.env.production.example` | Modify | P6 flags | 8 |

**Total:** ~400 lines prod/CI + ~90 lines tests. **Zero** rule engine. **Zero** new graph nodes (guard inline in grounding).

---

## 7. Implementation order

```text
Day 1 — P6.1
  guard_pass.py + prompt + grounding_node wire + unit tests

Day 2 — P6.2a
  conftest split + mark integration tests
  verify unit suite passes without Postgres

Day 2–3 — P6.2b
  GitHub Actions workflow + E2E artifact assertions
  local integration script

Day 3 — P6.3 + P5 guard ops
  .env.production.example (platform + review)
  artifact/report ops rows for guard stats
```

---

## 8. Test plan

| Layer | Command | Expect |
|-------|---------|--------|
| Unit (no PG) | `pytest -m "not integration"` | All pass, 0 skipped |
| Integration | `pytest -m integration` | E2E + retrieval tests pass |
| Guard unit | `test_guard_pass.py` | Mock LLM supported true/false |
| CI | GitHub Actions | Both jobs green |
| Regression | `GUARD_PASS_ENABLED=false` | Same as pre-P6 report |

---

## 9. Definition of done (Sprint 6)

1. **Guard pass:** Rationale checked against grounded quotes via small LLM; failures downgraded, never dropped.  
2. **CI:** Unit tests run without Postgres; integration tests run in CI with pgvector service.  
3. **No skip-all:** Integration job fails loudly if PG down; unit job never depends on PG.  
4. **Prod env:** Platform `.env.production.example` documents `SESSION_STORE_BACKEND=postgres` + `MEMORY_STORE_BACKEND=postgres`.  
5. **Dynamic only:** No deterministic rule engine in guard path.  
6. Artifact + ops report include guard stats.

---

## 10. Explicit non-goals (Sprint 6)

- Regex/YAML guard rules  
- NLI entailment model  
- Guard re-running compare or retrieval  
- Postgres `review_runs` persistence (P5 optional)  
- P2 `sections[]` ingest / tombstone MCP  
- Java implementation  

---

## 11. What’s left after Sprint 6 (roadmap)

| Priority | Sprint | Scope |
|----------|--------|-------|
| High | **P2** | `IngestRequest.sections[]`, tombstone, `register_contract` — stable boundaries from Java |
| High | **Java sync** | Playbook metadata (`review_guidance`, `preferred_position`) on policy register |
| Medium | **P5b** | Optional `review_runs` Postgres + `REVIEW_PERSIST_ARTIFACT=true` |
| Medium | **Sprint 6 cleanup** | Remove deprecated flat `report.metadata` keys; dedupe `section_coverage` in stats |
| Low | **P15+** | NLI “contradicts playbook” pre-filter; bulk review tables |
| Low | **Observability** | Prompt/response logging, OpenTelemetry spans per node |

---

## 12. Quick reference — trust layers (after P6)

```text
Compare LLM     → judgment (dynamic playbook text + hints)
quote_validate  → substring check before merge
grounding_node  → MCP verify_quote (character-exact in doc store)
guard_pass      → LLM “rationale supported by quotes?” (P6)
ReviewArtifact  → full audit trail for replay (P5)
```

Each layer **downgrades** on failure; none silently drops findings (P4 grounding + coverage).

---

**Summary:** Sprint 6 adds the **last reasoning sanity check** (dynamic LLM guard, not rules), **fixes the test/CI gap** (split unit vs integration + Docker Postgres in CI), and **documents production session reliability** (Postgres backends already coded). P2 ingest remains the next **critical path** item after P6.
