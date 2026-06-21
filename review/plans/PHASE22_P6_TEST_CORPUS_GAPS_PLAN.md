# Phase 22 P6 — Test / Corpus Gaps

**Plan ID:** `DR-PHASE-22-P6-TEST-CORPUS-GAPS`  
**Priority:** P6 (quality gate — does not change review graph logic)  
**Impact:** **0 LLM calls** in unit CI; **reproducible gold eval**; **10/10 gate** on curated enterprise contract; **stable Postgres tests**  
**Depends on:** Phase 22 P5 (config/env alignment for reproducible benchmark runs), Phase 22 P1–P4 (pipeline quality)  
**Scope:** `temp_java_sync/beta_test/`, `fixtures/`, `document_core/tests/`, `review_agent/tests/`, CI workflow  
**Non-goals:** Full lawyer-validated 100-contract dataset, rule-engine compliance, replacing LLM compare, Java API changes, new graph nodes

**Note:** This is **Phase 22 P6** (test/corpus). Older **Phase 15 P6** (`PHASE15_P6_GUARD_PASS_CI_PLAN.md`) covered guard pass + CI — orthogonal; merge CI workflow in P6-8 if not already done.

---

## 0. Verified root cause (code + benchmarks + tests)

### Symptom → production impact

```text
Pipeline improvements (P1–P5) measured on scale benchmark
  → contracts generated procedurally in scale_corpus.py
  → expect_gap = (idx + index) % 3 != 0  (mechanical, not curated)
  → accuracy_score = weighted heuristic (gap_recall + coverage + false_nc)
  → no section-level gold verdicts (status set, policy family)
  → scale exit 0 if ≥10/12 contracts complete — no quality floor
  → only Cisco 6×5 has section EXPECTED map; pass threshold legal_hits >= 4 (not 6)
  → document_core tests always hit shared Postgres; TRUNCATE global tables
  → MCP dev server + pytest share localhost:5435/legalai → flaky / order-dependent
```

**Enterprise 40+ context:** We optimize against a **synthetic stress corpus**, not a **curated gold set**. Regressions on real 20-section × 43-policy behavior are invisible until manual scale runs; unit CI does not prove enterprise accuracy.

### Evidence (verified in repo)

| Source | Mechanism | Value / behavior | Implication |
|--------|-----------|------------------|-------------|
| `scale_corpus.py` L526–548 | `_contract_fixture()` | `expect_gap = (idx + index) % 3 != 0` | ~67% sections labeled gap by formula, not legal review |
| `scale_corpus.py` L357–467 | `SECTION_TEMPLATES` | Same 20 templates × 12 contract types | Weak/strong text reused; no per-scenario gold |
| `scale_corpus.py` L545–548 | `eval_labels` | `{category, expect_gap, title}` only | No `expect_statuses`, `policy_ref`, or `bad` set |
| `run_scale_benchmark.py` L56–59 | gap hit rule | NC/INC + `source=playbook_compare` | Counts any compare flag on gap section — not correct policy family |
| `run_scale_benchmark.py` L68–70 | `accuracy_score` | 50% gap_recall + 30% coverage + 20% anti-false-NC | Heuristic index, not lawyer score |
| `run_scale_benchmark.py` L329 | exit code | `0 if len(ok_results) >= 10` | **No min gap_recall / coverage gate** |
| `scale_benchmark_summary.json` | last run | `avg_gap_recall_pct: 51.1`, `avg_accuracy_score: 61.7` | Would pass exit 0 today despite low recall |
| `run_cisco_assessment.py` L28–58 | `EXPECTED` | 6 sections, status sets + `bad` | **Curated** — correct pattern |
| `run_cisco_assessment.py` L184 | exit | `0 if legal_hits >= 4` | **Allows 4/6 (6.7/10) as pass** |
| `fixtures/cisco/` | JSON fixtures | 6-section contract + policy JSON files | Only small ESG proof path |
| `test_review_e2e.py` | integration | Mock LLM + tiny `SAMPLE_CONTRACT` | Not enterprise scale |
| `document_core/tests/conftest.py` L44–51 | `store` fixture | `TRUNCATE ... CASCADE` every test | Wipes **all** tenants — conflicts with running MCP |
| `document_core/tests/*.py` | markers | **No** `@pytest.mark.integration` | Postgres tests run in default `pytest` or skip entire session |
| `review_agent/tests/conftest.py` L33–37 | integration | Only tests with marker get `pg_document_store` | Correct pattern — **not mirrored in document_core** |
| `.github/workflows/` | CI | **Absent** | No automated gate on PR |

### Root causes (precise)

| # | Root cause | File / mechanism | Effect |
|---|------------|------------------|--------|
| **RC-1** | **Procedural scale corpus** | `scale_corpus._contract_fixture` | Labels don't reflect lawyer-validated gaps; false positives/negatives in metrics |
| **RC-2** | **Heuristic scorer only on scale** | `run_scale_benchmark._score_contract` | `accuracy_score` looks authoritative but isn't comparable to Cisco `legal_score_10` |
| **RC-3** | **No gold fixture for 20×43** | Only `scale_corpus.py` in code | Can't diff runs on frozen contract text + labels |
| **RC-4** | **Scale has no quality gate** | exit ≥10 completed contracts | Ship regressions while "benchmark green" |
| **RC-5** | **Cisco gate too loose** | `legal_hits >= 4` | 67% pass masks section-level misses |
| **RC-6** | **E2E proof confined to 6×5** | No pytest for scale gold | Enterprise path unguarded in CI |
| **RC-7** | **Shared Postgres, global TRUNCATE** | `document_core/tests/conftest.py` | Flaky when MCP + tests share DB; not worker-isolated |
| **RC-8** | **Integration vs unit not split (document_core)** | All pg tests use `store` fixture unconditionally | Fast CI can't run unit-only; pg down skips everything |

**Already correct (do not re-implement):**

| Asset | Status |
|-------|--------|
| Cisco JSON fixtures + `EXPECTED` map | `fixtures/cisco/`, `run_cisco_assessment.py` |
| Real-world 4-section assessment | `run_real_world_assessment.py` (same EXPECTED pattern) |
| Review agent integration conftest | `review_agent/tests/conftest.py` marker pattern |
| Mock-LLM E2E | `test_review_e2e.py` |

---

## 1. Design principles (minimal production patch)

1. **Two corpus tiers** — **Gold** (frozen JSON, curated labels) vs **Stress** (procedural `scale_corpus`, throughput/load only).
2. **One scorer module** — Cisco `EXPECTED` schema and scale heuristic share `benchmark_score.py`; names make heuristic explicit.
3. **Gates are explicit** — exit codes tied to numeric floors; no silent "completed = pass".
4. **0 graph changes** — tests and fixtures only.
5. **Postgres: isolate, don't rewrite store** — separate test DB URL + integration marker; keep TRUNCATE for single-worker CI.
6. **CI: unit always, integration optional, gold nightly** — PR runs unit + mock integration; nightly runs Cisco + 1 gold scale contract with real LLM.

---

## 2. Target behavior (after P6)

```text
Corpus
  ├─ fixtures/scale/enterprise_msa_gold.json     [NEW — curated 20-section gold]
  └─ scale_corpus.py                             [KEEP — stress/load, relabeled "heuristic"]

Scoring
  beta_test/benchmark_score.py                   [NEW]
    ├─ score_section_expected()  → Cisco / gold (status sets)
    └─ score_heuristic_gap()       → stress benchmark (explicit name)

Gates
  run_cisco_assessment.py     → exit 0 iff 6/6 sections (10/10)
  run_scale_benchmark.py      → --gate + floors; --gold-only for 1 contract 10/10
  pytest -m "not integration" → fast unit (document_core + review_agent)

Postgres
  TEST_DATABASE_URL=.../legalai_test             [NEW — separate from MCP dev DB]
  document_core tests → @pytest.mark.integration
```

---

## 3. Implementation tasks

### P6-1. Shared eval schema + scorer (~90 lines)

**File:** `temp_java_sync/beta_test/benchmark_score.py` (new)

```python
@dataclass(frozen=True)
class SectionEvalSpec:
    section_id: str
    topic: str
    expect_statuses: frozenset[str]   # e.g. NON_COMPLIANT, INCONCLUSIVE
    bad_statuses: frozenset[str] = frozenset()
    expect_gap: bool | None = None    # heuristic tier only
    policy_ref_hint: str = ""
    note: str = ""

def score_section_expected(
    findings_by_section: dict[str, dict],
    specs: dict[str, SectionEvalSpec],
) -> tuple[int, list[dict], float]:
    """Returns (hits, section_results, score_10)."""

def score_heuristic_gap(
    findings_by_section: dict[str, dict],
    eval_labels: dict[str, dict],
) -> dict[str, float | int]:
    """Current scale _score_contract logic — renamed, unchanged weights."""
```

**Wire:** `run_cisco_assessment.py`, `run_real_world_assessment.py`, `run_scale_benchmark.py` import scorer (delete duplicated loops).

**Acceptance:** Cisco and scale produce identical scores to today when using same inputs.

---

### P6-2. Curated gold fixture — one 20-section enterprise contract (~120 lines JSON + doc)

**Files:**

- `temp_java_sync/fixtures/scale/enterprise_msa_gold.json` — **frozen** contract (copy from `scale-enterprise-msa-2026` scenario in `scale_corpus.py` with fixed text, no codegen)
- `temp_java_sync/fixtures/scale/enterprise_msa_eval.json` — section-level gold:

```json
{
  "contract_ref": "scale-enterprise-msa-2026",
  "sections": {
    "2": {
      "topic": "Supplier Code of Conduct",
      "expect_statuses": ["NON_COMPLIANT", "INCONCLUSIVE"],
      "bad_statuses": ["COMPLIANT"],
      "policy_ref_hint": "playbook-compliance-rba-v2026"
    },
    "6": {
      "topic": "Information Security and MSS",
      "expect_statuses": ["NON_COMPLIANT", "INCONCLUSIVE"],
      "bad_statuses": ["COMPLIANT"],
      "policy_ref_hint": "playbook-security-mss"
    }
  },
  "gap_sections_minimum_hits": 12,
  "strong_sections_max_false_nc": 2
}
```

**Curation rule (minimal v1):** Manually label **14 gap sections** (from templates marked weak) + **6 strong sections** using same `expect_statuses` / `bad_statuses` pattern as Cisco. Full lawyer review is v2 — v1 is **engineer-curated against known weak/strong template text**.

**Do not** delete `scale_corpus.py` — add module docstring: *"Stress corpus — heuristic labels only; not gold eval."*

---

### P6-3. Scale benchmark gates (~45 lines)

**File:** `run_scale_benchmark.py`

**CLI flags:**

| Flag | Default | Purpose |
|------|---------|---------|
| `--gate` | off | Enforce quality floors on exit |
| `--gold-only` | off | Run single `enterprise_msa_gold.json` + eval JSON |
| `--min-avg-gap-recall` | 65.0 | Gate floor (stress mode) |
| `--min-avg-coverage` | 70.0 | Gate floor |
| `--min-contracts-ok` | 12 | Completeness |

**Gold-only mode exit:**

```python
# Section-level: score_10 >= 9.0 on curated gap sections (≥12/14)
# OR strict: all labeled gap sections hit expect_statuses
exit 0 iff gold_gap_hits >= gold_gap_sections - 2  # allow 2 misses initially; tighten to 0
```

**Stress mode with `--gate`:**

```python
exit 0 iff (
    len(ok_results) >= min_contracts_ok
    and avg_gap_recall >= min_avg_gap_recall
    and avg_coverage >= min_avg_coverage
)
```

**Report fields:** `benchmark_tier: "gold" | "stress"`, `gate_enabled`, `gate_passed`.

---

### P6-4. Cisco gate tighten (~5 lines)

**File:** `run_cisco_assessment.py` L184

```python
# Before: return 0 if legal_hits >= 4 else 1
# After:  return 0 if legal_hits == len(EXPECTED) else 1
```

Add `--min-score 10` optional flag for transitional CI (default **10/10**).

---

### P6-5. Pytest gold smoke (mock + live split) (~70 lines)

**File:** `review_agent/tests/test_scale_gold_smoke.py` (new)

| Test | Marker | Behavior |
|------|--------|----------|
| `test_gold_eval_schema_loads` | unit | Parse `enterprise_msa_eval.json` |
| `test_gold_scorer_on_fixture_findings` | unit | Frozen findings JSON → expected hits |
| `test_gold_contract_review_mock_llm` | integration | Load gold contract + policies subset; mock compare → ≥N sections covered |

**File:** `temp_java_sync/beta_test/run_scale_gold.py` (new, ~40 lines)

Thin wrapper: sync gold fixture → `run_review` → `score_section_expected` → exit code. Used for **nightly** with real LLM.

---

### P6-6. Postgres test isolation (~35 lines)

**File:** `document_core/tests/conftest.py`

1. Add module-level marker for pg tests:

```python
# At top of each test file using store fixture, OR session autouse:
pytestmark = pytest.mark.integration
```

Prefer **one line in conftest**:

```python
def pytest_collection_modifyitems(items):
    for item in items:
        if "store" in item.fixturenames or "pg_engine" in item.fixturenames:
            item.add_marker(pytest.mark.integration)
```

2. **Separate test database:**

```python
def _database_url() -> str:
    return os.environ.get(
        "TEST_DATABASE_URL",
        os.environ.get("DATABASE_URL", "postgresql://legalai:legalai@localhost:5435/legalai_test"),
    )
```

3. Document in `document_core/tests/README.md` (~15 lines): MCP uses `legalai`; tests use `legalai_test`; create with `createdb legalai_test`.

4. **Optional hardening:** prefix `tenant_id=f"pytest-{uuid4().hex[:8]}"` in all ingest tests (many already use `"t1"`, `"cat-tenant"` — isolated by TRUNCATE today; unique tenant helps when TRUNCATE removed later).

**Do not** add per-worker schemas in v1 — separate DB + integration marker is sufficient.

---

### P6-7. pytest.ini + CI split (~80 lines)

**Files:**

- `document_core/pytest.ini` (new):

```ini
[pytest]
asyncio_mode = auto
markers =
    integration: requires Postgres (TEST_DATABASE_URL)
```

- `review_agent/pytest.ini` (new or extend): add `benchmark: live LLM gold run (nightly)`

- `.github/workflows/review-ci.yml` (new):

```yaml
jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e document_core -e review/review_agent
      - run: cd review/review_agent && pytest -m "not integration" -q

  integration:
    services:
      postgres: ...
    env:
      TEST_DATABASE_URL: postgresql://...
    steps:
      - run: cd document_core && pytest -m integration -q
      - run: cd review/review_agent && pytest -m integration -q
```

**Nightly workflow (optional P6-7b):** `run_cisco_assessment.py` + `run_scale_gold.py` with secrets.

---

### P6-8. Stress vs gold documentation in benchmark output (~10 lines)

**File:** `run_scale_benchmark.py` summary JSON

```json
{
  "benchmark_tier": "stress",
  "eval_type": "heuristic_expect_gap",
  "gold_available": "fixtures/scale/enterprise_msa_gold.json",
  "gate": { "enabled": true, "passed": false, "floors": { ... } }
}
```

Prevents misreading `accuracy_score` as lawyer validation.

---

## 4. File touch list

| File | Change | Est. lines |
|------|--------|------------|
| `beta_test/benchmark_score.py` | **New** — shared scorer | +90 |
| `fixtures/scale/enterprise_msa_gold.json` | **New** — frozen contract | +120 |
| `fixtures/scale/enterprise_msa_eval.json` | **New** — curated labels | +80 |
| `beta_test/scale_corpus.py` | Docstring + export stress label | +5 |
| `beta_test/run_scale_benchmark.py` | Gates, `--gold-only`, use scorer | +55 |
| `beta_test/run_scale_gold.py` | **New** — gold runner | +40 |
| `beta_test/run_cisco_assessment.py` | 10/10 gate + scorer import | +10 |
| `beta_test/run_real_world_assessment.py` | Scorer import | +5 |
| `document_core/tests/conftest.py` | TEST_DATABASE_URL + auto-marker | +25 |
| `document_core/pytest.ini` | **New** | +6 |
| `document_core/tests/README.md` | **New** | +15 |
| `review_agent/tests/test_scale_gold_smoke.py` | **New** | +70 |
| `review_agent/tests/test_benchmark_score.py` | **New** | +50 |
| `.github/workflows/review-ci.yml` | **New** | +65 |

**Total:** ~640 lines (incl. fixtures). **No graph topology change.**

---

## 5. Tests (must pass)

| Test | Setup | Assert |
|------|-------|--------|
| `test_score_section_expected_cisco_pattern` | Mock findings + 6 specs | score_10 matches manual calc |
| `test_score_heuristic_gap_matches_legacy` | Frozen findings + scale eval_labels | Same metrics as old `_score_contract` |
| `test_gold_eval_schema_loads` | Load JSON | 20 sections, ≥14 gap specs |
| `test_gold_scorer_gap_section_hit` | NC finding on §2 | hit=True |
| `test_gold_scorer_bad_compliant` | COMPLIANT on gap § | hit=False |
| `test_document_core_auto_integration_marker` | Collect tests | store tests marked integration |
| **Regression** | `pytest -m "not integration"` review_agent | 196+ pass |
| **Regression** | Cisco E2E | 6/6 (after gate tighten) |

```powershell
cd Legal\review\review_agent
python -m pytest tests/test_benchmark_score.py tests/test_scale_gold_smoke.py -m "not integration" -q

cd Legal\document_core
set TEST_DATABASE_URL=postgresql://legalai:legalai@localhost:5435/legalai_test
python -m pytest -m integration -q
```

---

## 6. Verification (E2E)

| Run | Before P6 | Target after P6 |
|-----|-----------|-----------------|
| Cisco assessment | Pass at 4/6 | **6/6 required** (10/10) |
| Scale stress `--gate` | Pass at 10/12 complete | **Fails** if gap_recall < 65% until P1–P5 hold |
| Scale gold-only | N/A | **≥12/14** gap sections hit on curated labels |
| document_core pytest (MCP running) | Flaky TRUNCATE | Stable on `legalai_test` |
| PR CI | None | Unit job green without Postgres |

```powershell
cd Legal\temp_java_sync
python beta_test\run_cisco_assessment.py
python beta_test\run_scale_gold.py
python beta_test\run_scale_benchmark.py --gate --min-avg-gap-recall 65 --min-avg-coverage 70
```

---

## 7. Rollout / risk

| Risk | Mitigation |
|------|------------|
| Cisco 10/10 gate fails initially | Fix pipeline first (P1–P5); use `--min-score 8` transition 1 sprint |
| Gold labels wrong | v1 engineer-curated; version eval JSON; lawyer review v2 |
| `legalai_test` DB not created | CI creates DB; README for local |
| Stress benchmark always fails `--gate` | Gate opt-in (`--gate`); stress remains diagnostic |
| Duplication with Phase 15 P6 CI plan | Merge workflows; P15 guard tests stay separate |

**Rollback:** Remove `--gate`; restore Cisco `>= 4`; keep scorer module (no behavior change).

---

## 8. Implementation checklist

- [x] **P6-1** Shared `benchmark_score.py`
- [x] **P6-2** Gold fixture + eval JSON (1 enterprise contract)
- [x] **P6-3** Scale `--gate` + `--gold-only`
- [x] **P6-4** Cisco 10/10 gate (`--min-score 10`)
- [x] **P6-5** Pytest gold smoke + `run_scale_gold.py`
- [x] **P6-6** Postgres `TEST_DATABASE_URL` + integration auto-marker
- [x] **P6-7** pytest.ini + CI workflow
- [x] **P6-8** Benchmark tier metadata in summary JSON
- [x] **P6-9** Unit tests green
- [x] **P6-10** Cisco + scale gold E2E sign-off (2026-06-16: Cisco **10/10 PASS**; gold **16/20 legal, 11/14 gap hits — gate FAIL**, infra OK after tenant_id fix)

---

## 9. Corpus tier reference

| Tier | Source | Labels | Use |
|------|--------|--------|-----|
| **Gold** | `fixtures/scale/enterprise_msa_gold.json` + eval JSON | Curated `expect_statuses` / `bad` | Release gate, nightly, 10/10 |
| **Cisco gold** | `fixtures/cisco/` | 6-section EXPECTED | Supplier ESG regression |
| **Stress** | `scale_corpus.py` (12×20) | `expect_gap` heuristic | Load, discovery stress, trend metrics |
| **Unit** | `tests/fixtures.py` SAMPLE_* | Mock LLM | CI speed |

---

## 10. Relationship to prior plans

| Plan | Overlap | P6 action |
|------|---------|-----------|
| Phase 22 P1–P5 | Pipeline quality | Gates measure P1–P5 effect on **gold** corpus |
| Phase 15 P6 CI | Postgres CI | P6-7 implements workflow; reuse guard tests |
| Phase 21 P2R | Controlled corpus test | P6-2 gold fixture satisfies reranker eval need |
| Phase 10B golden E2E | Mock LLM path | Extend with scale gold smoke |

**P6 completes Phase 22 validation stack:** P1–P5 fix behavior; **P6 proves it on curated corpus with explicit gates.**

---

*End of Phase 22 P6 plan — test / corpus gaps.*
