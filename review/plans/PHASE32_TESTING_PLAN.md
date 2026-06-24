# Phase 32 — Testing (Integration, Load, Edge Cases)

**Status:** COMPLETE  
**Plan ID:** `DR-PHASE-32-TESTING`  
**Priority:** P2  
**Scope:** Python only — `document_core/tests`, `review_agent/tests`, `review_agent/scripts`, CI tweak  
**Estimated diff:** ~180 LOC across 6 files (mostly new tests + 1 script)  
**Depends on:** Phase 25 (pgvector), Phase 26 (metadata at ingest), Phase 31 (`review_wall_ms` optional assert)  
**Non-goals:** Java, frontend, k6/Gatling, real-embedding CI (slow), chaos platform

---

## 1. Goal

Close **real gaps** in test coverage — not duplicate what already ships.

Prove:

1. Category SQL filter roundtrip (positive + negative)
2. Review path hits **real Postgres** via in-process document-mcp (already mostly true)
3. Concurrent reviews do not break MCP client / store
4. Edge-case inputs do not crash parser/classify (unit-level)

---

## 2. What already exists (do not rebuild)

| Asset | Location | Status |
|-------|----------|--------|
| pgvector harness + `store` fixture | `document_core/tests/conftest.py` | **Done** — migrations, TRUNCATE, auto `@pytest.mark.integration` on `store` |
| review_agent pg fixtures | `review_agent/tests/conftest.py` | **Done** — `pg_engine`, `pg_document_store` |
| Category ingest + list | `test_ingest_search.py::test_ingest_auto_tags_policy` | **Done** — liability positive |
| Category search service | `test_search_request_metadata.py` | **Done** — `search_policy_by_categories` |
| Category after delete | `test_delete_policy.py::test_delete_policy_category_filter_excludes_deleted` | **Done** |
| Policy freshness + categories | `test_policy_freshness.py` | **Done** |
| P26 metadata unit tests | `test_metadata_at_ingest.py` | **Done** |
| Review integration E2E (mock LLM) | `test_review_e2e.py` (3 tests) | **Done** — ASGITransport + real pg lifespan |
| CI integration job | `.github/workflows/review-ci.yml` L53–91 | **Done** — pgvector service + both packages |
| Integration runner scripts | `review_agent/scripts/run_integration_tests.sh` | **Done** |

**Count:** 43+ unit test files in `review_agent/tests`; document_core has 15+ integration tests via `store`.

---

## 3. Verified gaps (accurate)

| # | Gap | Evidence | Risk |
|---|-----|----------|------|
| G1 | No **negative** category filter test (`privacy` → empty) | `test_ingest_search` only asserts liability hit | Wrong SQL `OR` logic undetected |
| G2 | review_agent CI may use **wrong DB** | CI sets `TEST_DATABASE_URL`; `review_agent/conftest.py` only `setdefault("DATABASE_URL", .../legalai)` | Tests hit dev DB name, not `legalai_test` |
| G3 | No assertion **retrieval hits ≥ 1** on pgvector discovery path | `test_review_graph_contract_only_discovery` checks findings, not `policy_hits` | Empty retrieval masked by compare mock |
| G4 | No **concurrent** `run_review` smoke | Only `test_llm_gateway_rate_limit` concurrency | Pool / shared store races |
| G5 | No edge-case fixture tests | Only `tests/fixtures.py` (MSA samples) | Unicode / 32K / empty categories |
| G6 | No load script | — | Manual perf checks only |

**Not a gap:** new `conftest_integration.py` (T1 in old plan) — harness exists.

**Deferred:** full embedding vector roundtrip with `EMBEDDING_ENABLED=true` — optional nightly; CI stays `RERANKER_BACKEND=lexical`, embeddings mocked in `test_pgvector_save_document.py`.

---

## 4. Task map (minimal, ordered)

| # | Task | Files | LOC | Notes |
|---|------|-------|-----|-------|
| **T1** | Fix `TEST_DATABASE_URL` in review_agent conftest | `review_agent/tests/conftest.py` | ~8 | Align with document_core |
| **T2** | Negative category filter test | `document_core/tests/test_ingest_search.py` | ~20 | One function |
| **T3** | Retrieval hits integration assert | `review_agent/tests/test_review_e2e.py` | ~25 | Extend discovery test |
| **T4** | Concurrent review smoke | `review_agent/tests/test_review_e2e.py` | ~40 | `asyncio.gather` × 3 |
| **T5** | Edge-case unit tests | `review_agent/tests/test_edge_cases.py` (NEW) | ~60 | Inline strings OK |
| **T6** | Load script | `review_agent/scripts/load_test_reviews.py` (NEW) | ~70 | Local/manual |
| **T7** | CI env fix + doc | `review-ci.yml`, `scripts/run_integration_tests.sh` | ~10 | Set `DATABASE_URL` from `TEST_DATABASE_URL` |

**Skip:** `conftest_integration.py`, new `test_category_filter_integration.py`, new `test_review_retrieval_integration.py` (extend existing files instead).

---

## 5. T1 — Align review_agent DB URL (`conftest.py`)

### Problem

```python
# review_agent/tests/conftest.py (current)
os.environ.setdefault("DATABASE_URL", ".../legalai")  # dev DB
```

CI integration job sets only `TEST_DATABASE_URL=.../legalai_test`.

### Change

Mirror `document_core/tests/conftest.py`:

```python
def _database_url() -> str:
    return os.environ.get(
        "TEST_DATABASE_URL",
        os.environ.get(
            "DATABASE_URL",
            "postgresql://legalai:legalai@localhost:5435/legalai_test",
        ),
    )

@pytest.fixture(scope="session", autouse=True)
def _database_url_for_tests():
    os.environ["DATABASE_URL"] = _database_url()
    os.environ.setdefault("DOCUMENT_STORE_BACKEND", "pgvector")
    ...
```

Update `database_url` fixture to call `_database_url()`.

---

## 6. T2 — Category filter negative (`test_ingest_search.py`)

Add **one** test to existing file (no new module):

```python
@pytest.mark.asyncio
async def test_category_filter_excludes_other_categories(store: PgVectorDocumentStore):
    tenant = "cat-negative"
    result = await ingest_document(
        IngestRequest(
            tenant_id=tenant,
            title="Liability Policy",
            kind=DocumentKind.POLICY,
            sections=[IngestSectionInput(section_id="1", title="Cap", text="Liability cap text.")],
        ),
        store=store,
    )
    assert result.document_id in store.list_document_ids_by_categories(tenant, ["liability"])
    assert result.document_id not in store.list_document_ids_by_categories(tenant, ["privacy"])
```

Uses P26 auto-tag from title — no new fixture file.

---

## 7. T3 — Retrieval hits on pgvector (`test_review_e2e.py`)

Extend `test_review_graph_contract_only_discovery` (or add sibling test) — **after** `run_review`:

```python
bundles = result.get("section_retrieval_by_id") or {}
assert bundles, "expected section retrieval bundles"
hit_sections = [
    sid for sid, raw in bundles.items()
    if (raw.get("policy_hits") or [])
]
assert hit_sections, "expected at least one section with policy_hits from pgvector"
```

Still mocks `invoke_structured` — **no live LLM**.

---

## 8. T4 — Concurrent review smoke (`test_review_e2e.py`)

```python
@pytest.mark.asyncio
@pytest.mark.integration
async def test_concurrent_reviews_smoke(monkeypatch):
    # same LLM mocks as test_review_graph_text_e2e
    # seed one policy via client.index_policy(...)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        await _seed_policy(client)  # small helper or inline

        async def one_review(i: int):
            return await run_review(
                client=client,
                tenant_id="demo",
                contract_text=SAMPLE_CONTRACT,
                contract_title=f"MSA-{i}",
                contract_type="msa",
                thread_id=f"concurrent-{i}",
            )

        results = await asyncio.gather(*[one_review(i) for i in range(3)])
    assert len(results) == 3
    assert all(r.get("report") and r["report"].findings for r in results)
```

Unique `thread_id` per run avoids state collision (P30: no checkpointer).

---

## 9. T5 — Edge cases (unit only, no new pg tests)

### New: `review_agent/tests/test_edge_cases.py`

Use **inline strings** (minimal; optional tiny files later). No new `fixtures/edge_cases/` directory required for DoD if tests are self-contained.

| Test | Input | Assert |
|------|-------|--------|
| `test_unicode_section_parses` | `parse_text_to_tree` with emoji in text | `len(tree.sections) >= 1`, no exception |
| `test_empty_categories_ingest_resolves` | `resolve_ingest_categories(title="Misc", ...)` | `categories == ["general"]` or auto-tag |
| `test_long_section_classify_truncates` | `IndexedChunk` with 33_000 char text, `classify_section_policies` mocked LLM | no crash; text in prompt ≤ `section_classify_max_chars` |
| `test_mixed_language_no_crash` | parse + lexical classify on EN/DE snippet | no exception |

**Do not** run full `run_review` for edge cases — keeps tests fast and unit-only (`-m "not integration"`).

---

## 10. T6 — Load script (`scripts/load_test_reviews.py`)

Minimal CLI (~70 LOC):

```bash
python -m review_agent.scripts.load_test_reviews \
  --concurrency 3 --reviews 6 --tenant load-test
```

### Behavior

1. Require `DATABASE_URL` (or `TEST_DATABASE_URL`)
2. `ASGITransport` + in-process `mcp.document_server.main:app` (same as e2e)
3. Patch/monetkey `invoke_structured` to return minimal classify + compare (copy from `test_review_e2e`)
4. `asyncio.Semaphore(concurrency)` + `asyncio.gather` over `run_review`
5. Print: total wall time, success count, error count, p95 latency
6. `sys.exit(1)` if `errors / reviews > 0.10`

### Package entry

Add to `pyproject.toml` only if needed:

```toml
[project.scripts]
# optional — or run as python -m review_agent.scripts.load_test_reviews
```

Prefer `python -m review_agent.scripts.load_test_reviews` with `if __name__ == "__main__"` — **no pyproject change**.

### Doc

Add 8-line section to `review_agent/scripts/run_integration_tests.sh` header comment OR new `review_agent/tests/README.md` (minimal):

```markdown
## Load test (local)
DATABASE_URL=... python -m review_agent.scripts.load_test_reviews --concurrency 5 --reviews 10
```

---

## 11. T7 — CI tweak (`.github/workflows/review-ci.yml`)

In `review_agent integration` step, add:

```yaml
env:
  TEST_DATABASE_URL: postgresql://legalai:legalai@localhost:5435/legalai_test
  DATABASE_URL: postgresql://legalai:legalai@localhost:5435/legalai_test
  RERANKER_BACKEND: lexical
  EMBEDDING_ENABLED: "false"
```

After T1, only `TEST_DATABASE_URL` may suffice — set both for clarity.

**No new CI job** — extend existing `integration` job.

---

## 12. Files touched

| File | T1 | T2 | T3 | T4 | T5 | T6 | T7 |
|------|----|----|----|----|----|----|-----|
| `review_agent/tests/conftest.py` | ✓ | | | | | | |
| `document_core/tests/test_ingest_search.py` | | ✓ | | | | | |
| `review_agent/tests/test_review_e2e.py` | | | ✓ | ✓ | | | |
| `review_agent/tests/test_edge_cases.py` | | | | | ✓ | | |
| `review_agent/scripts/load_test_reviews.py` | | | | | | ✓ | |
| `review_agent/tests/README.md` | | | | | | ✓ | |
| `.github/workflows/review-ci.yml` | | | | | | | ✓ |

**Not touched:** Java, frontend, research agent, new `conftest_integration.py`.

---

## 13. Definition of done

- [x] `pytest -m integration` passes locally with Postgres on `:5435` / `legalai_test`
- [x] `pytest -m "not integration"` includes new edge-case tests (fast)
- [x] CI `integration` job green (document_core + review_agent)
- [x] `test_category_filter_excludes_other_categories` passes
- [x] `test_concurrent_reviews_smoke` passes
- [x] Load script exits 0 on local stack; exits 1 when `--reviews 1` forced to fail (manual check)
- [x] Unit CI job unchanged — no Postgres required

---

## 14. Implementation order

```
T1 (conftest URL) → T2 (negative category) → T3+T4 (e2e) → T5 (edge unit) → T7 (CI) → T6 (load script)
```

Single PR acceptable; T6 can be follow-up (manual tool).

---

## 15. Running tests (reference)

```powershell
# Unit only (no Postgres)
cd Legal\review\review_agent
python -m pytest -m "not integration" -q --noconftest

# Integration (Postgres + legalai_test)
$env:TEST_DATABASE_URL="postgresql://legalai:legalai@127.0.0.1:5435/legalai_test"
bash scripts/run_integration_tests.sh

# document_core integration
cd Legal\document_core
python -m pytest -m integration -q
```

---

## 16. Out of scope

| Item | Reason |
|------|--------|
| `fixtures/edge_cases/*.txt` files | Inline strings sufficient for minimal DoD |
| Real embedding + hybrid search CI | Slow/flaky; mock in `test_pgvector_save_document` |
| `test_review_retrieval_integration.py` separate file | Extend `test_review_e2e.py` |
| Java catalog contract tests | Phase 32 non-goals |
| k6 / Gatling | Non-goals |

---

## 17. Risk register

| Risk | Mitigation |
|------|------------|
| ASGITransport lifespan + shared pg store under concurrency | Unique `thread_id`; TRUNCATE per test via `pg_document_store` fixture |
| Load script imports review_agent graph (slow) | Acceptable for manual tool; not in CI |
| `mcp.document_server` path | `review_agent/conftest.py` already adds `Legal ai` to `sys.path` |
| Concurrent test flaky on CI | Limit to 3 reviews; mock LLM |
