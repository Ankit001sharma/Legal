# Phase 17 — P0 Accuracy-Critical Fixes

**Plan ID:** `DR-PHASE-17-P0`  
**Owner:** Youngser  
**Scope:** `document_core`, `review_agent`, Dev UI bootstrap scripts — **P0 only** (retrieval + LLM runtime)  
**Goal:** Eliminate total retrieval failure and LLM import crashes so section-first review always reaches real playbook compare.  
**Depends on:** Phase 10 production pipeline, Phase 16 structured ingest  
**Estimate:** ~180 lines code + ~120 lines tests + 0.5 sprint day  
**Status:** P0-1 and P0-2 implemented — Youngser sign-off pending local E2E

---

## 0. Executive summary

Two P0 defects caused **~3.5/10** review accuracy in Youngser’s E2E runs despite healthy Postgres, sync, and policies indexed:

| ID | Defect | Effect when broken |
|----|--------|-------------------|
| **P0-1** | `SearchRequest` schema ↔ MCP handler mismatch | **100% retrieval failure** on category search → `playbook_compare_count: 0` |
| **P0-2** | `langchain` not reliably installed at runtime | Review **crashes** or classifier/routing **silent-fail** → gap-LLM guesses without policies |

**Youngser solution:** Fix schema contract + packaging/install path first; add regression tests and single-process MCP guardrails so fixes cannot be masked by stale servers.

---

## 1. P0 bug register (finding → root cause → optimal solution)

### P0-1 — Category policy retrieval returns HTTP 500

#### Finding (observed)

```
POST /tools/search_policy_by_categories → 500
detail: 'SearchRequest' object has no attribute 'metadata'
```

Downstream in review agent:

| Metric | Broken | Fixed (clean MCP) |
|--------|--------|-------------------|
| `retrieval_zero_hit_sections` | 4 | 0 |
| `playbook_compare_count` | 0 | 3 |
| Policy quotes in findings | None | Present |
| Findings source | Gap-LLM fallback | Real compare |

Beta assessment (`temp_java_sync/outputs/beta_assessment.json`): **3.5/10** broken → **7/10** after clean restart.

#### Root cause (precise)

1. **Caller contract:** `review_agent/clients/document_client.py` L93–101 merges categories into JSON:
   ```python
   payload["metadata"] = {**(payload.get("metadata") or {}), "categories": categories}
   ```
2. **Handler expectation:** `Legal ai/mcp/document_server/main.py` L262–266:
   ```python
   categories = (request.metadata or {}).get("categories") or []
   ```
3. **Schema gap:** `document_core/schemas/chunk.py` `SearchRequest` originally had **no** `metadata` field. FastAPI/Pydantic parses body → model instance without attribute → `AttributeError` at handler entry.
4. **False-negative testing:** Stale duplicate process on port **8003** (two PIDs listening) served **pre-fix** code after file edit — Youngser’s runs looked “still broken” until old process killed.

**Classification:** Core Python bug in `document_core` schema/API contract — **not** fixture data, **not** Java sync.

#### Youngser solution (optimal)

| Step | Action | Rationale |
|------|--------|-----------|
| 1 | Add `metadata: dict[str, Any] = Field(default_factory=dict)` to `SearchRequest` | Single source of truth; backward-compatible default `{}` |
| 2 | Add integration test: POST with `metadata.categories` → 200 + non-empty when policies indexed | Prevents regression |
| 3 | Add unit test in `document_core/tests/test_multi_retrieval.py` asserting model accepts metadata | Schema-level guard |
| 4 | Document in `document_core/README` or MCP comment: categories **must** travel via `metadata.categories` until dedicated field added | API clarity |
| 5 | Ops: `start_document_mcp.ps1` prints PID; add preflight kill of existing `:8003` listener (optional script flag) | Prevents stale-server false negatives |

**Do not:** Add a second parallel `categories` field on `SearchRequest` without migrating caller + handler — duplicates contract.

**Alternative considered (rejected):** Pass categories as query param on MCP URL — breaks existing client and Java parity.

---

### P0-2 — `langchain` missing at runtime; editable install broken

#### Finding (observed)

```
ModuleNotFoundError: No module named 'langchain'
  at review_agent/models/llm_gateway.py:27
  from langchain.chat_models import init_chat_model
```

Also:

```
contract routing LLM attempt 1 failed: 'ReviewSettings' object has no attribute 'review_plan_llm_max_tokens'
classify batch failed: No module named 'langchain'
section classify LLM failed for 1..4: No module named 'langchain'
```

Review graph aborts at `final_gap_verify` or runs with classifier fallback → `categories: ["general"]` → wrong retrieval.

#### Root cause (precise)

1. **Import site:** `get_review_model()` lazy-imports `langchain.chat_models.init_chat_model` — hard dependency on **`langchain`** package (not just `langchain-core`).
2. **Declared but not installed:** `review_agent/pyproject.toml` lists `langchain>=0.3.0` but Youngser’s Python 3.14 env had only `langchain-core` from transitive deps.
3. **Install path broken:**
   ```toml
   document-core @ file:../../document_core
   ```
   Resolves to `D:\Ankit_legal\document_core` (wrong) instead of `D:\Ankit_legal\Legal\document_core` → `pip install -e review_agent` **fails** → deps never pulled.
4. **Mistral provider:** Runtime uses `LLM_PROVIDER=mistralai` but `langchain-mistralai` not declared in `pyproject.toml` — latent P0 on Mistral-only envs.
5. **Cascade:** Classifier LLM failure → `_fallback_result()` in `section_classifier.py` L44–49 forces `categories=["general"]` → category-filtered retrieval misses `liability` / `indemnification` playbooks even when P0-1 fixed.

**Classification:** Packaging + runtime dependency bug in `review_agent` — **not** LLM quality.

#### Youngser solution (optimal)

| Step | Action | Rationale |
|------|--------|-----------|
| 1 | Fix `pyproject.toml` path: `document-core @ file:../../../Legal/document_core` **or** relative from repo: `file:../../document_core` verified from `Legal/review/review_agent` | Enables `pip install -e .` |
| 2 | Add explicit deps: `langchain>=0.3.0`, `langchain-mistralai>=1.0.0` | Matches runtime providers |
| 3 | Add `requirements-dev.txt` or lock snippet in `review_agent/README` with install one-liner | Reproducible Youngser setup |
| 4 | Update `temp_java_sync/run_dev_ui.ps1` to `pip install langchain langchain-mistralai` if import fails (already partial) | Dev UI bootstrap safety |
| 5 | Add `scripts/install_review_deps.ps1` at repo level: editable `document_core` + `review_agent` + langchain stack | Single command for Youngser |
| 6 | CI: `python -c "from langchain.chat_models import init_chat_model"` in review-ci workflow | Catch missing dep before merge |

**Do not:** Vendor/copy `init_chat_model` — maintenance burden; use declared deps.

**Related (fixed, include in sprint):** `review_plan_llm_max_tokens` missing from `ReviewSettings` — already added L36 `config.py`; add test that `get_settings()` exposes field.

---

## 2. Implementation plan (Youngser execution order)

### Sprint slice — Day 0.5

```text
P0-1 schema + tests
    → P0-1 MCP restart verification
        → P0-2 pyproject + install script
            → P0-2 CI import smoke
                → Full E2E + beta_assessment re-run
```

---

### Task P0-1.1 — Harden `SearchRequest.metadata` (Youngser)

**File:** `document_core/document_core/schemas/chunk.py`

```python
class SearchRequest(BaseModel):
    ...
    metadata: dict[str, Any] = Field(default_factory=dict)
```

**Acceptance:**

- [ ] `SearchRequest(tenant_id="t", query="q", metadata={"categories": ["liability"]})` validates
- [ ] Unknown keys in metadata do not crash handler

**Status:** ✅ Already in tree — **verify + test only**

---

### Task P0-1.2 — Regression tests (Youngser)

**File:** `document_core/tests/test_search_request_metadata.py` (new, ~40 lines)

| Test | Assert |
|------|--------|
| `test_search_request_accepts_metadata_categories` | Model round-trip |
| `test_search_policy_by_categories_http` | `@pytest.mark.integration` POST `/tools/search_policy_by_categories` → 200, not 500 |

**File:** `review/review_agent/tests/test_document_client_categories.py` (new, ~35 lines)

| Test | Assert |
|------|--------|
| `test_search_policy_by_categories_payload_shape` | Mock httpx; payload contains `metadata.categories` |

**Acceptance:**

- [ ] Tests fail if `metadata` field removed from schema
- [ ] Integration test skipped without Postgres (marker existing pattern)

---

### Task P0-1.3 — MCP handler defensive guard (Youngser, optional 5 lines)

**File:** `Legal ai/mcp/document_server/main.py` L262–266

```python
categories = list((getattr(request, "metadata", None) or {}).get("categories") or [])
```

**Rationale:** Belt-and-suspenders during rollout if old clients omit field. Remove after one release.

**Acceptance:**

- [ ] Old server code path documented as unsupported

---

### Task P0-1.4 — Ops: single MCP instance (Youngser)

**File:** `Legal/Legal ai/scripts/start_document_mcp.ps1`

Add before uvicorn:

```powershell
$existing = netstat -ano | Select-String ":8003.*LISTENING"
if ($existing) {
    Write-Host "WARNING: port 8003 already in use. Stop old document-mcp first."
    Write-Host $existing
}
```

**Acceptance:**

- [ ] Youngser runbook: one listener on 8003 before E2E
- [ ] Document in `temp_java_sync/README.md` troubleshooting section

---

### Task P0-2.1 — Fix editable install path (Youngser)

**File:** `review/review_agent/pyproject.toml`

Verify relative path from `Legal/review/review_agent/`:

```toml
dependencies = [
    "document-core @ file:../../document_core",
    ...
    "langchain>=0.3.0",
    "langchain-mistralai>=1.0.0",
]
```

Run from repo:

```powershell
pip install -e "d:\Ankit_legal\Legal\document_core"
pip install -e "d:\Ankit_legal\Legal\review\review_agent[dev]"
```

**Acceptance:**

- [ ] Both editable installs succeed on Youngser Windows Python 3.11–3.14
- [ ] `python -c "from langchain.chat_models import init_chat_model; import review_agent"` OK

---

### Task P0-2.2 — Install script (Youngser)

**File:** `Legal/review/review_agent/scripts/install_deps.ps1` (new)

```powershell
# Youngser solution: one-shot review stack deps
$Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
pip install -e (Join-Path $Root "document_core")
pip install -e (Join-Path $Root "review\review_agent") langchain langchain-mistralai
python -c "from langchain.chat_models import init_chat_model; print('langchain OK')"
```

Wire into:

- `temp_java_sync/run_dev_ui.ps1` — call if import fails
- `.github/workflows/review-ci.yml` — use pip install -e before pytest

**Acceptance:**

- [ ] Fresh venv → script → E2E passes without manual pip

---

### Task P0-2.3 — Config field regression test (Youngser)

**File:** `review/review_agent/tests/test_config.py` (extend)

```python
def test_review_plan_llm_max_tokens_default():
    s = ReviewSettings()
    assert s.review_plan_llm_max_tokens == 1024
```

**Acceptance:**

- [ ] Contract routing can read `settings.review_plan_llm_max_tokens` without AttributeError

---

### Task P0-2.4 — Classifier fallback observability (Youngser, P0-adjacent)

**File:** `review_agent/services/section_classifier.py`

When `_fallback_result` used, log **WARNING** with `classify_warning` propagated to review `warnings[]`.

**Acceptance:**

- [ ] E2E report shows explicit warning if classifier fell back to `general` (not silent accuracy loss)

*Note:* Full fix of fallback logic is P1 — this task makes P0 failures visible.

---

## 3. Verification matrix (Youngser sign-off)

Run after all P0 tasks:

```powershell
# Terminal 1
cd "d:\Ankit_legal\Legal\Legal ai\scripts"
.\start_postgres_podman.ps1
.\start_document_mcp.ps1   # confirm ONE listener on 8003

# Terminal 2
cd "d:\Ankit_legal\Legal\review\review_agent"
.\scripts\install_deps.ps1

cd "d:\Ankit_legal\Legal\temp_java_sync"
python beta_test\run_assessment.py
python run_full_e2e.py
```

| Gate | Pass criteria |
|------|---------------|
| **G1** | `search_policy_by_categories` → HTTP **200** |
| **G2** | `retrieval_zero_hit_sections` = **0** (NDA fixture) |
| **G3** | `playbook_compare_count` ≥ **3** |
| **G4** | `gap_llm_failed` = **0** |
| **G5** | No `ModuleNotFoundError: langchain` in logs |
| **G6** | No `review_plan_llm_max_tokens` AttributeError |
| **G7** | Beta overall score ≥ **7/10** |

---

## 4. Rollout & rollback

| Step | Action |
|------|--------|
| Deploy 1 | Merge P0-1 schema + tests → restart **all** document-mcp instances |
| Deploy 2 | Merge P0-2 packaging → run `install_deps.ps1` on every dev/CI agent |
| Rollback | Revert schema field only if handler updated to use dedicated `categories: list[str]` field instead |

**Youngser rule:** Never run E2E against document-mcp without confirming `/tools/search_policy_by_categories` returns 200 on a smoke POST.

---

## 5. Out of scope (P1 — separate plan)

These **lower accuracy** but are **not P0** (pipeline completes):

- P1-4: Classifier fallback `categories=["general"]` when LLM fails
- P1-5: LLM enum typo `INSUFFICIENT_POLIC_CONTEXT` on `SectionCompareItem`
- P1-6: Rationale guard over-downgrade to INCONCLUSIVE
- P1-7: Dev UI findings JSON path mismatch (`0 findings` display)

Track under `DR-PHASE-18-P1` when P0 gates all green.

---

## 6. File touch list

| File | P0 task | Lines (est.) |
|------|---------|--------------|
| `document_core/document_core/schemas/chunk.py` | P0-1.1 | ✅ done |
| `document_core/tests/test_search_request_metadata.py` | P0-1.2 | +40 |
| `review_agent/tests/test_document_client_categories.py` | P0-1.2 | +35 |
| `Legal ai/mcp/document_server/main.py` | P0-1.3 | +3 |
| `Legal ai/scripts/start_document_mcp.ps1` | P0-1.4 | +8 |
| `review_agent/pyproject.toml` | P0-2.1 | +2 |
| `review_agent/scripts/install_deps.ps1` | P0-2.2 | +15 |
| `review_agent/tests/test_config.py` | P0-2.3 | +8 |
| `review_agent/services/section_classifier.py` | P0-2.4 | +5 |
| `.github/workflows/review-ci.yml` | P0-2.2 | +5 |
| `temp_java_sync/README.md` | P0-1.4 | +20 |

**Total:** ~180 lines new/modify + ~120 test lines

---

## 7. Definition of done (Youngser)

- [ ] P0-1 and P0-2 bug register items **closed** with merged code + tests
- [ ] `beta_test/run_assessment.py` exits 0 with `retrieval_zero_hit_sections: 0`
- [ ] `run_full_e2e.py` exits 0 twice in a row on Youngser machine
- [ ] Runbook updated: kill duplicate `:8003`, run `install_deps.ps1`, then Dev UI
- [ ] CI green on review + document_core integration markers

---

## 8. Youngser solution checklist (use every fix)

When starting any P0 fix PR, prefix commit/PR title:

```text
Youngser P0: <short description>
```

When starting implementation of each task:

1. **Youngser solution:** state root cause in PR description (copy from §1)
2. Add/adjust test that **failed before fix**
3. Restart document-mcp if touching `document_core` or MCP
4. Re-run `beta_test/run_assessment.py` and attach score to PR

---

*End of Phase 17 P0 plan — Youngser*
