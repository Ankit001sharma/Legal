# Sprint 5 — Silence & Trust (P5.1–P5.4)

**Plan ID:** `DR-PHASE-14-P5`  
**Scope:** Review agent Python (+ thin platform mirror)  
**Goal:** Every review run is **reproducible and auditable** from a single `ReviewArtifact` JSON — no silent pipeline steps, report ≠ raw LLM dump.  
**Depends on:** Phase 10 section-first pipeline, Phase 12 P3 retrieval meta, Phase 13 P4 playbook/grounding  
**Estimate:** ~320 lines prod code, ~240 lines tests, **3–4 days**  
**Explicitly excluded:** New graph nodes, Java `review_runs` API (optional P5.2b behind flag), full policy chunk text in artifact, NLI/replay engine

---

## 0. Why this sprint (production trust)

Harvey/Ironclad/Kira-style contract review products treat **audit trail + ops visibility** as first-class:

| Production requirement | Your gap today (verified) |
|------------------------|---------------------------|
| Replay why a finding exists | `section_compare_items` + `section_retrieval_by_id` live in LangGraph state but **never exported** in API artifacts |
| Debug retrieval misses | P3 `retrieval_meta.attempts[]` exists per section — **count-only** in report metadata |
| Know what was superseded | `superseded_ids` computed in `final_verify_llm.py` L419–420, used in-node, **discarded** — only `superseded_count` kept |
| SRE / legal ops dashboard | No ops block in markdown; stats scattered across `compliance_stats`, `final_verify_stats`, `section_coverage` |
| Report ≠ LLM transcript | `render_markdown_report` dumps findings only — no executive summary or pipeline health |

**Silence** = steps that ran but leave no durable trace. **Trust** = lawyer + SRE can answer “why?” without re-running the graph.

---

## 1. Problem statement (verified in code)

| ID | Gap | Current code |
|----|-----|--------------|
| P5.1 | No canonical audit object | `ReviewState` has 30+ keys (`review_state.py` L14–54); `report_node` copies **aggregates** only (`nodes.py` L325–347) |
| P5.1 | Superseded finding IDs lost | `final_gap_verify_node` filters by `superseded_set` (`section_compare_nodes.py` L145–151) but never writes IDs to state |
| P5.1 | Gap LLM not in compare trail | Gap findings have `metadata.final_verify=gap_llm` (`final_verify_llm.py` L53) but not grouped in export |
| P5.2 | Platform artifacts incomplete | `ReviewAgent.execute` returns `artifacts.report` only (`legal_ai_platform/.../review_agent.py` L78–82) — no audit JSON |
| P5.3 | Markdown is findings-only | `reports/generator.py` L8–34 — no summary paragraph, no ops section |
| P5.4 | Ops metrics not normalized | `retrieval_retry_sections`, `backfill_count`, `grounding_failed` exist in scattered dicts — no single `ops` block |
| P5.4 | `rule_findings_count` N/A | P4 removed rule engine — use **`playbook_compare_count`** (findings with `metadata.source=playbook_compare`) |

**Already strong (keep):**

- Full pipeline payloads on state: `section_retrieval_by_id`, `section_compare_items`, `gap_section_ids`, `unclear_finding_ids`, `conflict_pairs`  
- P3 per-section `retrieval_meta.attempts[]`  
- P4 `metadata.source` on findings (`playbook_compare`, `conflict_resolver`)  
- `ReviewReport.metadata` as extensible dict — **formalize**, don’t replace schema  

---

## 2. Design principles

1. **Artifact = read model** — build once at `report_node` from existing state; **zero new graph nodes**.  
2. **Slim by default** — retrieval audit rows = refs + scores + attempts; **no full policy/contract text** in artifact (report findings already carry quotes).  
3. **Reproducibility ≠ re-execution** — artifact must contain enough to debug/replay decisions offline (inputs to each LLM step, supersession chain, ops counters).  
4. **Report synthesizer ≠ compare LLM** — markdown built from **findings + artifact stats**; optional 1-paragraph LLM summary behind flag (default off).  
5. **Minimal Python surface** — one schema file, one builder module, small generator + platform diff; fix known data-loss bugs (superseded IDs) in same PR.

---

## 3. Target pipeline (unchanged topology)

```text
… → grounding → report_node
                      ├─ build_review_artifact(state)     # P5.1 — NEW helper
                      ├─ report.metadata.artifact = …     # P5.2
                      ├─ render_markdown_report(report, artifact)  # P5.3/P5.4
                      └─ return report
```

Platform orchestrator mirrors `artifacts.audit` from `report.metadata.artifact`.

---

## 4. `ReviewArtifact` schema (P5.1)

### 4.1 New file

**File:** `review_agent/schemas/review_artifact.py` (~90 lines)

```python
ARTIFACT_VERSION = "1.0"

class SectionAuditRow(BaseModel):
    section_id: str
    title: str = ""
    char_count: int = 0
    categories: list[str] = Field(default_factory=list)

class RetrievalAuditRow(BaseModel):
    section_id: str
    categories: list[str] = Field(default_factory=list)
    hit_count: int = 0
    hits: list[RetrievalHitRef] = Field(default_factory=list)  # doc_id, section_id, score — NO text
    retrieval_meta: dict[str, Any] = Field(default_factory=dict)  # attempts[], final_attempt, counts

class GapLlmAuditRow(BaseModel):
    section_id: str
    finding_id: str
    status: str
    rationale_preview: str = ""  # first 200 chars

class ReviewArtifactOps(BaseModel):
    retrieval_retry_sections: int = 0
    retrieval_max_attempts_used: int = 0
    retrieval_zero_hit_sections: int = 0
    llm_batches_failed: int = 0
    gap_llm_sections: int = 0
    gap_llm_failed: int = 0
    unclear_recompared: int = 0
    conflicts_recompared: int = 0
    conflicts_unresolved: int = 0
    superseded_count: int = 0
    ungrounded_count: int = 0
    grounding_downgraded_count: int = 0
    backfill_count: int = 0
    post_grounding_backfill_count: int = 0
    playbook_compare_count: int = 0
    policy_conflict_count: int = 0

class ReviewArtifact(BaseModel):
    artifact_version: str = ARTIFACT_VERSION
    run_id: str  # thread_id
    pipeline: str = "section_first"
    generated_at: datetime
    tenant_id: str
    contract_document_id: str
    contract_title: str

    sections: list[SectionAuditRow]
    routing: dict[str, Any]  # topics, contract_type, mode
    discovery: dict[str, Any]  # discovered_policy_document_ids, fetched_policy_refs, warnings

    retrieval: list[RetrievalAuditRow]
    compare_items: list[SectionCompareItem]
    work_queue: dict[str, Any]  # gap_section_ids, unclear_finding_ids, conflict_pairs

    gap_llm: list[GapLlmAuditRow]
    superseded_finding_ids: list[str]

    final_verify_stats: dict[str, Any]
    section_coverage: dict[str, Any]
    compliance_stats: dict[str, Any]
    ops: ReviewArtifactOps
```

**Size control (config):**

| Variable | Default | Purpose |
|----------|---------|---------|
| `ARTIFACT_INCLUDE_HIT_REFS` | `true` | Per-hit doc/section/score rows |
| `ARTIFACT_MAX_HIT_REFS_PER_SECTION` | `10` | Cap hit refs (full hits still in state) |

Do **not** embed `policy_hits[].parent_chunk.text` — keeps artifact ~50–200 KB vs multi-MB.

### 4.2 Builder

**File:** `review_agent/services/review_artifact.py` (~120 lines)

```python
def build_review_artifact(state: ReviewState, *, findings: list[ComplianceFinding] | None = None) -> ReviewArtifact:
    """Pure function — no I/O. Single source of truth for audit export."""
```

**Data sources (read-only from state):**

| Artifact field | State key(s) |
|----------------|--------------|
| `sections` | `section_review_sections` or `contract_sections` |
| `routing.topics` | `contract_routing` |
| `discovery` | `discovered_policy_document_ids`, `fetched_policy_refs`, `discovery_warnings`, `indexed_policies` (refs/titles only) |
| `retrieval` | `section_retrieval_by_id` → slim rows via `_slim_retrieval_bundle()` |
| `compare_items` | `section_compare_items` (validate as `SectionCompareItem`) |
| `work_queue` | `gap_section_ids`, `unclear_finding_ids`, `conflict_pairs` |
| `gap_llm` | findings where `metadata.final_verify == "gap_llm"` (pass `grounded_findings` or pre-grounding `findings` + merge) |
| `superseded_finding_ids` | **`state["superseded_finding_ids"]`** (new — see §4.3) |
| `ops` | derived — see P5.4 |

**Helper `_slim_retrieval_bundle(bundle)`:**

```python
hits = [
  {"document_id": str(h.parent_chunk.document_id), "section_id": h.parent_chunk.section_id, "score": h.score}
  for h in bundle.policy_hits[:max_refs]
]
# retrieval_meta copied as-is (attempts ladder from P3)
```

### 4.3 Python fix — persist superseded IDs (required for P5.1)

**File:** `graph/section_compare_nodes.py` — `final_gap_verify_node` return dict (+2 lines):

```python
return {
    ...
    "superseded_finding_ids": list(dict.fromkeys(superseded_ids)),
}
```

**File:** `state/review_state.py` — add:

```python
superseded_finding_ids: list[str]
```

**Why:** Without this, audit trail cannot explain which findings final verify replaced — highest-value, lowest-cost fix.

### 4.4 Optional state key (v1 recommended)

Add `review_artifact: dict[str, Any]` to `ReviewState` — set in `report_node` so platform can read from graph result without parsing nested metadata. **Alternative:** only `report.metadata.artifact` (smaller diff). **Recommend:** metadata only for v1; add state key if platform needs it before report serialization.

### 4.5 Acceptance (P5.1)

- [ ] `build_review_artifact(state)` returns valid `ReviewArtifact` from mocked state fixture.  
- [ ] `retrieval[].retrieval_meta.attempts` present when P3 retry ran.  
- [ ] `compare_items` round-trips identical to state `section_compare_items`.  
- [ ] `superseded_finding_ids` populated after gap/unclear/conflict verify.  
- [ ] No policy/contract full text in artifact JSON (snapshot test).

---

## 5. Store artifact (P5.2)

### 5.1 Primary store — `report.metadata.artifact` (v1)

**File:** `graph/nodes.py` — `report_node` (~15 lines):

```python
from review_agent.services.review_artifact import build_review_artifact

artifact = build_review_artifact(state, findings=findings)
report = ReviewReport(
    ...
    metadata={
        ...
        "artifact": artifact.model_dump(mode="json"),
        # Keep legacy flat keys for backward compat (deprecated — document in README)
        "compliance_stats": stats,
        ...
    },
)
report.summary_markdown = render_markdown_report(report, artifact=artifact)
```

**Backward compatibility:** Do **not** remove existing metadata keys in v1 — UI/clients may depend on `compliance_stats`, `final_verify_stats`. Add `artifact` as canonical; deprecate flat keys in Sprint 6 cleanup.

### 5.2 Platform mirror

**File:** `legal_ai_platform/src/legal_ai_platform/agents/review/review_agent.py` (~4 lines):

```python
artifacts={
    "report": report.model_dump(mode="json"),
    "audit": report.metadata.get("artifact"),
    ...
}
```

Session (optional, same PR if trivial):

**File:** `legal_ai_platform/.../session/service.py` — store `last_review_audit` alongside `last_review_report` when matter snapshot updated.

### 5.2b Postgres `review_runs` (optional — defer or flag)

**Not required for Sprint 5 Done.** When needed:

```sql
CREATE TABLE review_runs (
  id UUID PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  contract_document_id UUID NOT NULL,
  thread_id TEXT,
  artifact JSONB NOT NULL,
  report_summary TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

**File:** `review_agent/services/review_run_store.py` — insert when `REVIEW_PERSIST_ARTIFACT=true`.  
**Default:** `false` — metadata + platform artifacts sufficient for v1.

### 5.3 Acceptance (P5.2)

- [ ] `artifacts.report.metadata.artifact` present in platform `AgentResponse`.  
- [ ] `artifacts.audit` equals `metadata.artifact` (top-level convenience).  
- [ ] JSON serializable; `artifact_version` = `"1.0"`.  
- [ ] E2E test asserts artifact on full review mock path.

---

## 6. Report synthesizer (P5.3)

### 6.1 Problem

Lawyers need a **readable memo**, not a dump of every LLM compare row. Synthesis must come from **structured findings + artifact stats**, not re-invoking compare LLM.

### 6.2 Template-first markdown (default)

**File:** `reports/generator.py` — extend signature:

```python
def render_markdown_report(
    report: ReviewReport,
    *,
    artifact: ReviewArtifact | None = None,
) -> str:
```

**New sections (order):**

1. **Header** — existing (title, tenant, doc, structure confidence)  
2. **Executive summary** — `_render_executive_summary(report, artifact)` — **template only**, no LLM  
3. **Findings** — existing `_finding_block` loop (unchanged)  
4. **Pipeline ops** — `_render_ops_block(artifact)` — P5.4  
5. **Footer** — existing  

**Executive summary template (~8 lines):**

```markdown
## Executive summary

Reviewed **{reviewable_count}** contract sections against **{discovery_count}** discovered policies.
**{non_compliant}** non-compliant, **{critical}** critical, **{policy_conflict}** policy conflicts.
Retrieval retried **{retrieval_retry_sections}** section(s); **{backfill_count}** coverage backfill(s).
**{ungrounded_count}** finding(s) failed quote grounding and were downgraded or flagged.
```

Counts derived from `artifact.ops` + finding status scan on `report.findings` — **no LLM**.

### 6.3 Optional LLM summary paragraph (off by default)

| Variable | Default | Purpose |
|----------|---------|---------|
| `REPORT_LLM_SUMMARY` | `false` | One short paragraph after template summary |
| `REPORT_LLM_SUMMARY_MAX_TOKENS` | `256` | Cap cost |

**File:** `reports/summary_llm.py` (~45 lines) — only when flag true:

- Input: finding labels + statuses + ops counts ( **not** full contract text)  
- Output: 2–4 sentence paragraph appended under `## Executive summary`  
- Failure: fall back to template-only; add warning to report  

**Do not** pass `compare_items` or retrieval hits to summary LLM — prevents second judgment path.

### 6.4 Acceptance (P5.3)

- [ ] Default run: markdown contains Executive summary + Findings + Pipeline ops; **no** extra LLM call.  
- [ ] `REPORT_LLM_SUMMARY=true`: one paragraph appended; mock LLM test.  
- [ ] Summary counts match artifact ops (snapshot test).  
- [ ] Report does **not** include raw `compare_items` or retrieval hit text.

---

## 7. Report ops block (P5.4)

### 7.1 `ReviewArtifactOps` derivation

**File:** `review_agent/services/review_artifact.py` — `_build_ops(state, findings)`:

| Field | Source |
|-------|--------|
| `retrieval_retry_sections` | `compliance_stats.retrieval_retry_sections` |
| `retrieval_max_attempts_used` | `compliance_stats.retrieval_max_attempts_used` |
| `retrieval_zero_hit_sections` | `compliance_stats.retrieval_zero_hit_sections` |
| `llm_batches_failed` | `compliance_stats.llm_batches_failed` |
| `gap_llm_sections` | `final_verify_stats.gap_llm_sections` |
| `gap_llm_failed` | `final_verify_stats.gap_llm_failed` |
| `unclear_recompared` | `final_verify_stats.unclear_recompared` |
| `conflicts_recompared` | `final_verify_stats.conflicts_recompared` |
| `conflicts_unresolved` | `final_verify_stats.conflicts_unresolved` |
| `superseded_count` | `len(superseded_finding_ids)` |
| `ungrounded_count` | findings where `grounded is False` |
| `grounding_downgraded_count` | findings where `metadata.grounding_failed is True` |
| `backfill_count` | `section_coverage.backfill_count` |
| `post_grounding_backfill_count` | `section_coverage.post_grounding_backfill_count` |
| `playbook_compare_count` | findings where `metadata.source == "playbook_compare"` |
| `policy_conflict_count` | findings with `status == POLICY_CONFLICT` |

**Note:** User table says `rule_findings_count` — **do not implement** (no rule engine). Export `playbook_compare_count`; document alias in ops markdown for ops teams.

### 7.2 Markdown ops block

**File:** `reports/generator.py` — `_render_ops_block(artifact)`:

```markdown
## Pipeline operations

| Metric | Value |
|--------|------:|
| Retrieval retries (sections) | {retrieval_retry_sections} |
| Max retrieval attempts used | {retrieval_max_attempts_used} |
| Zero-hit sections | {retrieval_zero_hit_sections} |
| Compare LLM batches failed | {llm_batches_failed} |
| Gap LLM sections | {gap_llm_sections} |
| Superseded findings | {superseded_count} |
| Ungrounded findings | {ungrounded_count} |
| Grounding downgrades | {grounding_downgraded_count} |
| Coverage backfill | {backfill_count} |
| Post-grounding backfill | {post_grounding_backfill_count} |
| Playbook compare findings | {playbook_compare_count} |
| Policy conflicts | {policy_conflict_count} |
```

Also expose `artifact.ops` in JSON for dashboards — SRE reads JSON, legal ops reads markdown.

### 7.3 Python cleanup — normalize duplicate coverage (fix in builder)

Today `compliance_stats` nests `section_coverage` **and** state has top-level `section_coverage` (`section_compare_nodes.py` L188–192).  

**Builder rule:** `ReviewArtifact.section_coverage` = merge state `section_coverage` (includes post-grounding keys from P4). Do **not** duplicate nested copy from `compliance_stats.final_gap_verify.section_coverage` if identical.

Optional one-line fix in `final_gap_verify_node`: stop nesting `section_coverage` inside `compliance_stats` (breaking — defer to Sprint 6). Document in artifact builder: prefer top-level state key.

### 7.4 Acceptance (P5.4)

- [ ] `artifact.ops.ungrounded_count` matches count of `grounded=False` on final findings.  
- [ ] Ops markdown table renders all rows when artifact present.  
- [ ] `playbook_compare_count` > 0 on standard section-first review test.  
- [ ] `retrieval_retry_sections` reflects P3 retry when mocked.

---

## 8. File change matrix

| File | Action | Task | ~Lines |
|------|--------|------|--------|
| `schemas/review_artifact.py` | **Create** | P5.1 schema | 90 |
| `services/review_artifact.py` | **Create** | P5.1 builder + ops | 120 |
| `state/review_state.py` | Modify | `superseded_finding_ids` | 2 |
| `graph/section_compare_nodes.py` | Modify | persist superseded IDs | 3 |
| `graph/nodes.py` | Modify | attach artifact, pass to generator | 20 |
| `reports/generator.py` | Modify | summary + ops blocks | 55 |
| `reports/summary_llm.py` | **Create** (optional) | P5.3 LLM paragraph | 45 |
| `config.py` | Modify | artifact + summary flags | 10 |
| `review_agent/.env.example` | Modify | docs | 8 |
| `legal_ai_platform/.../review_agent.py` | Modify | `artifacts.audit` | 4 |
| `tests/test_review_artifact.py` | **Create** | P5.1/P5.4 | 120 |
| `tests/test_report_generator.py` | **Create** or extend | P5.3/P5.4 | 80 |
| `tests/test_final_gap_verify.py` | Modify | superseded in state | 15 |

**Total:** ~560 lines (incl. tests). **Zero** new graph nodes.

---

## 9. Implementation order

```text
Day 1 — P5.1 core
  review_artifact schema + builder + superseded_finding_ids state fix
  test_review_artifact.py (unit, no Postgres)

Day 2 — P5.2 + P5.4 ops
  report_node wiring + artifact.ops derivation
  platform artifacts.audit mirror

Day 3 — P5.3 synthesizer
  generator executive summary + ops markdown
  optional summary_llm.py behind flag

Day 4 — Integration + polish
  E2E artifact snapshot test
  fix duplicate coverage read in builder
  .env.example + README one paragraph
```

---

## 10. Test plan

| Layer | Tests |
|-------|--------|
| Unit | `test_review_artifact.py` — schema validation, slim retrieval, ops counts |
| Unit | `test_report_generator.py` — summary counts, ops table, no raw compare dump |
| Unit | `test_final_gap_verify.py` — superseded IDs in state after node |
| Mock E2E | Full graph result → `metadata.artifact.artifact_version == "1.0"` |
| Regression | Clients reading flat `metadata.compliance_stats` still work |
| Size | Artifact JSON < 500 KB on 40-section fixture (no hit text) |

---

## 11. Definition of done (Sprint 5)

1. **Full audit trail:** `ReviewArtifact` captures sections, routing, discovery, retrieval paths/hits (refs), compare items, gap LLM rows, superseded IDs.  
2. **Stored:** `report.metadata.artifact` + platform `artifacts.audit`.  
3. **Report ≠ LLM dump:** Markdown = executive summary (template) + findings + ops table; optional LLM paragraph off by default.  
4. **Ops visible:** Retries, ungrounded, backfill, playbook_compare, policy_conflict counts in `artifact.ops` and markdown.  
5. **Reproducible:** Given artifact JSON + contract/policy store, an engineer can trace every finding to compare item or gap LLM row and supersession chain.  
6. All new tests pass; no new graph nodes.

---

## 12. Explicit non-goals (Sprint 5)

- Postgres `review_runs` persistence (optional flag only)  
- Java audit API / webhook export  
- Artifact diff across runs  
- Storing full LLM prompts/responses (Phase 15+ observability)  
- Removing legacy flat metadata keys (Sprint 6)  
- Word/PDF export redesign  

---

## 13. Quick reference — artifact vs report

| Consumer | Read |
|----------|------|
| Lawyer UI | `report.findings` + `summary_markdown` |
| SRE / ops | `metadata.artifact.ops` or ops markdown section |
| Debug / replay | `metadata.artifact` full JSON |
| Downstream automation | `artifacts.audit` on `AgentResponse` |

| Field | Lawyer-visible? | In artifact? |
|-------|-----------------|--------------|
| Finding quotes | Yes | No (in report.findings only) |
| Compare items | No | Yes |
| Retrieval attempts | No | Yes |
| Superseded IDs | No | Yes |
| Policy hit full text | No | No |

---

## 14. Python problems fixed in this sprint (checklist)

| Problem | Fix |
|---------|-----|
| Superseded IDs discarded | `superseded_finding_ids` on state + artifact |
| Retrieval/compare payloads not exported | `artifact.retrieval` + `artifact.compare_items` |
| Scattered stats | `artifact.ops` single normalized block |
| Markdown silent on pipeline health | Executive summary + ops table |
| Platform missing audit | `artifacts.audit` |
| Untyped metadata growth | Typed `ReviewArtifact` sub-object |
| `rule_findings_count` obsolete | `playbook_compare_count` (document alias) |

---

**Summary:** Sprint 5 = **one typed audit JSON** built at report time from state you already collect, **plus** a synthesized markdown report for humans and ops. Minimal code, no new nodes, fixes the superseded-ID data loss bug, and matches production-grade contract-review products' traceability model.
