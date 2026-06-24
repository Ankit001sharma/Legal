# Phase 37B — Raw Text Section Parsing

**Status:** COMPLETE  
**Plan ID:** `DR-PHASE-37B-RAW-TEXT-PARSER`  
**Priority:** P0  
**Scope:** `document_core` only — `text_parser.py`, `chunk.py` validator, `ingest.py` warnings, tests  
**Estimated diff:** ~200–280 LOC (mostly tests + one module)  
**Depends on:** Phase 37A (complete)  
**Non-goals:** PDF parsing, Java changes, per-parent LLM categories (37C), chunking overlap (37D), `sections[]` ingest path changes

---

## 1. Goal

Java sends **one raw string** (`text`) with headings and body separated by newlines. Python **`parse_text_to_tree`** produces parent sections for chunking.

```
Java text (extracted, not PDF bytes)
    → normalize_extracted_text()   # strip noise
    → line scan + heading regex
    → DocumentTree (sections[], structure_confidence)
    → ingest → parent/child chunks
```

**No PDF library in Python.** Quality depends on Java preserving line breaks around headings.

---

## 2. Minimal-change strategy

| Do | Don't |
|----|-------|
| Edit **`text_parser.py`** only for parsing logic | New parser package or ML layout model |
| Small validator tweak in **`chunk.py`** | Change `sections_to_tree` / structured ingest |
| One extra warning line in **`ingest.py`** | Touch pgvector, review_agent, retrieval |
| New **`test_text_parser.py`** + reuse existing fixtures | Duplicate fixtures in `review_agent` |
| Flatten acme NDA JSON → raw text in **test helper** | Commit huge raw text blob if helper suffices |

**Single PR:** `phase-37b-raw-text-parser`

---

## 3. Java text format (document for Java team)

Recommended shape (one section block):

```
{section_id}. {Title Line}
{body paragraph(s)...}

{section_id}. {Next Title}
...
```

**Acme NDA example (what parser must handle):**

```
3. Confidential Information
"Confidential Information" means all non-public...

6. Limitation of Liability
Except as set forth in Section 7...

7. Defense and Indemnification
Vendor shall defend, indemnify...
```

**Expected parse result:** top-level `section_id` ∈ `3`, `6`, `7` (and `1`–`10` for full acme fixture) with title + body in `SectionNode.text`.

Also supported (already partial):

| Pattern | Example |
|---------|---------|
| Section prefix | `Section 4. Indemnification` |
| Article | `ARTICLE IV. Term` |
| Sub-clause | `12.2.1 Cap on damages` |
| Letter sub | `(a) Confidential Information` |
| MSA deep numbering | `12.2 Limitation of Liability` |

---

## 4. Current state (gap analysis)

File: `document_core/parser/text_parser.py`

| Area | Today | Gap |
|------|-------|-----|
| Single-number inline | `_HEADING_RE` `\d+\.\s+[A-Z]…` | Works for `6. Limitation of Liability` |
| Sub-number inline | `_INLINE_NUMBERED_RE` requires `\d+\.\d+` | `12.2.1` OK; `1. Title` uses `_HEADING_RE` not inline |
| Pre-clean | None | Page footers, soft hyphens break sections |
| Confidence HIGH | `len(sections) >= 3` | Plan: **≥5** top-level for HIGH |
| Confidence MEDIUM | 1–2 sections or 3–4 | Plan: **2–4** top-level |
| Ingest empty text | `require_text_or_sections` | Works; message not explicit (37B.1) |
| LOW warning | `ingest.py` L38–39 | Done; add section count in warning (37B.6) |
| Tests | `test_parse_numbered_sections` in `test_ingest_search.py` | No NDA golden, no noise tests |

**Acme acceptance:** flatten `temp_java_sync/fixtures/acme_nda/acme_cloudvendor_nda.json` → ≥8 top-level sections with ids `1`…`10`.

---

## 5. Implementation order

```
Step 1  normalize_extracted_text() + wire into parse_text_to_tree
Step 2  Heading regex tweaks (minimal)
Step 3  _assess_confidence thresholds
Step 4  IngestRequest validator message (37B.1)
Step 5  ingest.py warning enrichment (37B.6)
Step 6  test_text_parser.py + fixtures helper
Step 7  Run pytest document_core
```

---

## 6. Task detail

### 37B.1 — Reject whitespace-only `text`

**File:** `document_core/schemas/chunk.py`

**Change:** In `require_text_or_sections`, when `sections` empty:

```python
if not self.sections:
    if not (self.text or "").strip():
        raise ValueError("text is required and must be non-empty when sections[] is omitted")
```

**Note:** `self.text.strip()` already runs in same validator — order matters: strip first, then check.

**Test:** `test_ingest_rejects_whitespace_only_text` in `test_text_parser.py` or `test_ingest_search.py`.

| LOC | ~4 |
|-----|-----|

---

### 37B.4 — Pre-clean extracted text (do before regex)

**File:** `document_core/parser/text_parser.py`

Add **`normalize_extracted_text(text: str) -> str`** — call as first line in `parse_text_to_tree`.

| Rule | Implementation | Rationale |
|------|----------------|-----------|
| Normalize newlines | `text.replace("\r\n", "\n").replace("\r", "\n")` | Windows Java extract |
| De-hyphenate line breaks | `re.sub(r"-\s*\n\s*", "", text)` | `liabil-\nity` → `liability` |
| Drop page footers | `re.sub(r"(?im)^\s*page\s+\d+(\s+of\s+\d+)?\s*$", "", text)` | `Page 3 of 12` |
| Collapse excessive blank lines | `re.sub(r"\n{4,}", "\n\n\n", text)` | PDF spacers |
| Trim | `text.strip()` | |

**Do not:** collapse all internal spaces (breaks formatting). **Do not:** remove ALL-CAPS lines (may be headings — 37B.2).

| LOC | ~15 |
|-----|-----|

---

### 37B.2 + 37B.3 — Heading patterns (minimal regex edits)

**File:** `document_core/parser/text_parser.py`

#### 37B.2 — Extend `_HEADING_RE` (one alternation block)

Add to existing alternation (keep current patterns):

```python
# ALL-CAPS short title line (5–80 chars, ≥2 words) — NDA exhibit style
r"[A-Z][A-Z0-9\s/&,\-]{4,79}$"
```

Guard in `_match_heading`: require `line == line.upper()` and `len(line.split()) >= 2` so body sentences in ALL CAPS are not headings.

Optional (only if acme tests fail):

```python
# "Section 3." without title on same line — rare; skip unless needed
```

`Section 3.` / `ARTICLE IV` — **already covered** by `(?:section|article|…)`.

#### 37B.3 — Fix single-digit inline heading

**Problem:** `_INLINE_NUMBERED_RE = r"^(\d+(?:\.\d+)+)\s+(.+)$"` requires a dot-subnumber (`12.2`), not `6. Limitation`.

**Fix:** Add before inline check in `_match_heading`:

```python
simple_numbered = re.match(r"^(\d+)\.\s+([A-Z].{2,120})$", line)
if simple_numbered:
    section_id = simple_numbered.group(1)
    title = f"{section_id}. {simple_numbered.group(2).strip()}"
    return section_id, title, 1
```

Avoid double-match with `_HEADING_RE` — call simple_numbered **after** `_HEADING_RE.match` fails OR merge into one ordered `_match_heading` chain.

**Hierarchy:** keep existing `_heading_level` / stack logic unchanged.

| LOC | ~25 |
|-----|-----|

---

### 37B.5 — `structure_confidence` thresholds

**File:** `document_core/parser/text_parser.py` — `_assess_confidence`

Count **top-level** sections only: `len(sections)` (not children), matching acme NDA flat structure.

| Top-level count | Confidence |
|-----------------|------------|
| ≥ 5 | `HIGH` |
| 2–4 | `MEDIUM` |
| 1 + `len(full_text) > 2000` | `LOW` (blob) |
| 1 + shorter | `MEDIUM` (small doc) |
| 0 | `LOW` (fallback `body` node) |

```python
def _assess_confidence(sections: list[SectionNode], full_text: str) -> StructureConfidence:
    n = len(sections)
    if n >= 5:
        return StructureConfidence.HIGH
    if n >= 2:
        return StructureConfidence.MEDIUM
    if n == 1 and len(full_text) > 2000:
        return StructureConfidence.LOW
    if n >= 1:
        return StructureConfidence.MEDIUM
    return StructureConfidence.LOW
```

**Impact:** `SAMPLE_CONTRACT` (~4 top-level) stays **MEDIUM**; acme NDA (10) → **HIGH**. Update `test_parse_numbered_sections` if it asserts `high` — may need `>= 2` only or use acme fixture for HIGH assertion.

| LOC | ~8 |
|-----|-----|

---

### 37B.6 — Ingest warnings

**File:** `document_core/services/ingest.py`

After parse (L38–39), enrich when LOW:

```python
if tree.structure_confidence == StructureConfidence.LOW:
    warnings.append(
        f"structure_confidence=low: {len(tree.sections)} section(s) from text; headings may be incomplete"
    )
```

Optional MEDIUM guard for single-section long policy:

```python
elif len(tree.sections) < 2 and not request.sections:
    warnings.append("structure_confidence=medium: only one section detected from text")
```

Keep minimal — **one** enriched LOW message is enough for 37B.

| LOC | ~5 |
|-----|-----|

---

### 37B.7 — Golden test: NDA (acme)

**New file:** `document_core/tests/test_text_parser.py`

**Helper** (in test file, not production):

```python
def _flatten_sections_json(sections: list[dict]) -> str:
    blocks = []
    for s in sections:
        blocks.append(f"{s['section_id']}. {s['title']}\n{s['text']}")
    return "\n\n".join(blocks)
```

Load: `Path(__file__).resolve().parents[2] / "temp_java_sync/fixtures/acme_nda/acme_cloudvendor_nda.json"`  
—or copy minimal 10-section JSON into `document_core/tests/fixtures/acme_nda_sections.json` to avoid cross-package path (preferred for CI).

**Assertions:**

```python
def test_parse_acme_nda_raw_text():
    raw = _flatten_sections_json(ACME_SECTIONS)
    tree = parse_text_to_tree(document_id=uuid4(), title="NDA", text=raw)
    ids = {n.section_id for n in tree.sections}
    assert len(tree.sections) >= 8
    assert "3" in ids
    assert "6" in ids
    assert "7" in ids
    assert tree.structure_confidence == StructureConfidence.HIGH
    liability = next(n for n in tree.sections if n.section_id == "6")
    assert "Limitation of Liability" in liability.title
    assert "twelve (12) months" in liability.text or "fees paid" in liability.text.lower()
```

| LOC | ~60 |
|-----|-----|

---

### 37B.8 — Golden test: MSA

Reuse `document_core/tests/fixtures.py` → `SAMPLE_CONTRACT`.

```python
def test_parse_msa_numbered_sections():
    tree = parse_text_to_tree(document_id=uuid4(), title="MSA", text=SAMPLE_CONTRACT)
    ids = {n.section_id for n in tree.sections}
    assert "12.2" in ids or any("12.2" in n.section_id for n in _walk(tree.sections))
    assert "13" in ids or any("indemnif" in n.title.lower() for n in _walk(tree.sections))
    assert tree.structure_confidence in {StructureConfidence.MEDIUM, StructureConfidence.HIGH}
```

Add `_walk` helper for nested `12.2.1` children.

**Migrate:** move or duplicate assertion from `test_ingest_search.py::test_parse_numbered_sections` → dedicated parser test; leave ingest test as smoke only.

| LOC | ~35 |
|-----|-----|

---

### 37B.9 — Golden test: policy playbook

Reuse `SAMPLE_POLICY` from same fixtures file.

```python
def test_parse_policy_playbook_sections():
    tree = parse_text_to_tree(document_id=uuid4(), title="Policy", text=SAMPLE_POLICY)
    assert len(tree.sections) >= 2
    titles = " ".join(n.title.lower() for n in tree.sections)
    assert "liability" in titles
    assert "indemnif" in titles
```

| LOC | ~20 |
|-----|-----|

---

### 37B.4 (test) — Noise normalization

```python
def test_normalize_strips_page_footers_and_dehyphenates():
    raw = "3. Confidential Information\nliabil-\nity cap.\n\nPage 2 of 10\n\n4. Term\nOne year."
    tree = parse_text_to_tree(document_id=uuid4(), title="T", text=raw)
    assert len(tree.sections) >= 2
    assert "liability" in tree.sections[0].text.replace("\n", " ")
```

| LOC | ~15 |
|-----|-----|

---

## 7. File checklist

| File | Action |
|------|--------|
| `document_core/parser/text_parser.py` | normalize + regex + confidence |
| `document_core/schemas/chunk.py` | validator message |
| `document_core/services/ingest.py` | warning text |
| `document_core/tests/test_text_parser.py` | **new** — all golden tests |
| `document_core/tests/fixtures/acme_nda_sections.json` | **new** — copy 10 sections from acme fixture (avoids temp_java_sync path) |
| `document_core/tests/test_ingest_search.py` | relax or point to parser tests |

**Do not modify:** `parent_child.py`, `pgvector_store.py`, `review_agent/**`, Java.

---

## 8. Acceptance criteria

| # | Criterion |
|---|-----------|
| AC1 | `IngestRequest(text="   ")` raises clear error when `sections[]` empty |
| AC2 | Acme NDA flattened raw text → ≥8 top-level sections, ids include `3`, `6`, `7`, confidence `HIGH` |
| AC3 | `SAMPLE_CONTRACT` → liability + indemnity sections detectable |
| AC4 | `SAMPLE_POLICY` → ≥2 sections |
| AC5 | `Page N of M` lines removed; soft hyphen join works |
| AC6 | `pytest document_core/tests/test_text_parser.py` green |
| AC7 | Existing `document_core` unit tests still pass (update confidence assertion if needed) |

---

## 9. Risk & rollback

| Risk | Mitigation |
|------|------------|
| ALL-CAPS heuristic false positives | Require ≥2 words + length cap; skip lines ending with `.` mid-sentence |
| Confidence downgrade for small contracts | 2–4 sections = MEDIUM still ingests; only affects warning metadata |
| Over-aggressive de-hyphenation | Only `-\n` pattern, not mid-line hyphens |
| Duplicate heading matchers | Single `_match_heading` ordered chain; unit tests lock behavior |

**Rollback:** Revert one PR; no DB/schema migration.

---

## 10. Effort estimate

| Step | Hours |
|------|-------|
| normalize + regex | 2–3h |
| confidence + ingest warnings | 0.5h |
| tests + acme fixture copy | 2–3h |
| CI fix / assertion updates | 0.5h |
| **Total** | **1–1.5 dev days** |

---

## 11. Out of scope (later phases)

| Item | Phase |
|------|-------|
| Per-parent LLM category tags | 37C |
| Child chunk overlap / max size | 37D |
| Java PDF extraction quality guide | 39 |
| `structure_confidence` blocking ingest | not planned — warn only |

---

## 12. PR description template

```
Phase 37B: raw text section parsing for Java ingest

- normalize_extracted_text (page lines, soft hyphens)
- heading regex for NDA/MSA single-number lines
- structure_confidence: HIGH≥5, MEDIUM 2-4
- ingest warnings + explicit empty text validation
- golden tests: acme NDA, MSA, policy playbook
```
