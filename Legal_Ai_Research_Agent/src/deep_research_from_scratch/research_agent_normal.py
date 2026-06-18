"""Normal Research Pipeline — fast legal answers with 2-3 retrieval rounds.

This is the lightweight sibling of the full deep-research pipeline.
Goal: produce a concise, verified answer (500-1200 words) in a fraction of
the time needed for a full legal memorandum.

Pipeline:
    load_memory
        ↓
    compact_conversation
        ↓
    write_research_brief  (same brief, but tells the researcher to be concise)
        ↓
    normal_researcher     (simple search→fetch loop, 2-3 rounds max)
        ↓
    generate_normal_answer
        ↓
    END
"""

from __future__ import annotations

import re

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from deep_research_from_scratch.config import config as app_config
from deep_research_from_scratch.model_config import (
    ainvoke_with_retry,
    get_chat_model,
)
from deep_research_from_scratch.research_agent_scope import (
    compact_conversation,
    load_memory,
    write_research_brief,
)
from deep_research_from_scratch.mcp_client import get_retrieval_client
from deep_research_from_scratch.retrieval_bridge import (
    format_fetch_result,
    format_retrieval_results,
)
from deep_research_from_scratch.source_registry import (
    RetrievedSource,
    citation_label,
    extract_case_names,
    extract_citations,
    filter_citable_sources,
    format_writer_source_registry,
    merge_retrieved_sources,
    source_from_fetch,
    sources_from_search_hits,
)
from deep_research_from_scratch.state_scope import AgentInputState, AgentState
from deep_research_from_scratch.utils import get_today_str

# ── LLM instances ──────────────────────────────────────────────────────────────

_answer_model = get_chat_model("writer", max_tokens=4096)
_search_model = get_chat_model("reasoning", temperature=0.0)

# ── Prompts ────────────────────────────────────────────────────────────────────

_PLANNER_PROMPT = """\
You are an Indian legal research assistant. Given the research brief below,
generate exactly {max_queries} search queries covering DIFFERENT angles.

MANDATORY query types — include ALL of these:
1. STATUTORY TEXT query — fetch the actual statute section from indiacode.nic.in
   Format: site:indiacode.nic.in "[Full Act Name]" [section keyword]
   Example: site:indiacode.nic.in "Bharatiya Nyaya Sanhita" section 61 conspiracy
   ⚑ This produces the authoritative statute page — the researcher will fetch it directly.

2. LEADING SUPREME COURT precedent query — target the most authoritative SC ruling
   Format: site:indiankanoon.org [legal issue] supreme court [landmark OR leading]
   Example: site:indiankanoon.org bail BNSS section 480 supreme court leading
   ⚑ CRITICAL: This MUST return an actual judgment page (URL contains /doc/), NOT a search page.
   If the first result is a search page, the researcher will follow the /doc/ links within it.

3. RECENT SUPREME COURT query — post-2022 SC rulings showing current position
   Format: site:indiankanoon.org [legal issue] supreme court 2023 2024
   Example: site:indiankanoon.org anticipatory bail supreme court 2024

4. RECENT HIGH COURT query — post-2022 judgments from relevant jurisdiction
   Format: site:indiankanoon.org [issue] high court 2023 2024
   Example: site:indiankanoon.org bail rejection grounds high court 2023 2024

5. BROADER STATUTE search — adjacent Acts that may also apply
   Example: site:indiacode.nic.in "Public Examinations" "Unfair Means" 2024

6. DIRECT INDIANKANOON search — without site: restriction for reliability fallback
   Format: [legal issue] India judgment [relevant year]

JUDGMENT PAGES — NOT SEARCH PAGES (CRITICAL):
- IndianKanoon judgment pages have URLs like: indiankanoon.org/doc/12345678/
- IndianKanoon search pages have URLs like: indiankanoon.org/search/?formInput=...
- The researcher can only extract FACTS, HOLDINGS, and RATIO from actual judgment pages.
- Always prefer queries that land directly on /doc/ pages.

SPECIAL RULES:
- For EXAM FRAUD / NEET / paper leak matters: ALWAYS include a query for
  "Public Examination (Prevention of Unfair Means) Act 2024 indiacode.nic.in"
- For BNS matters: ALWAYS use BNS section numbers from indiacode.nic.in, NOT IPC numbers
  (e.g. BNS §61 = conspiracy, NOT §120-B; BNS §111 = organised crime, NOT §386)
- Use BROADER terminology — cover synonyms and alternate provision names.

Research brief:
{brief}

Date: {date}

Return ONLY the queries, one per line, no numbering, no extra text.
"""

_ANSWER_PROMPT = """\
You are an Indian legal research assistant writing a concise legal answer.

Research brief:
{brief}

Retrieved sources and findings:
{findings}

Source registry (the ONLY sources you may cite):
{source_registry}

Date: {date}
{topic_checklist}
══════════════════════════════════════════
CRITICAL RULES — NON-NEGOTIABLE
══════════════════════════════════════════

1. TOOLS FIRST — NO MEMORY ANSWERS
   This answer must be grounded entirely in the retrieved sources above.
   Do NOT answer from training data or general legal memory.
   If the retrieved sources are insufficient to answer the question fully,
   explicitly state: "The retrieved sources did not establish [point] —
   independent verification required." Do NOT fill gaps from memory.

2. CITE EVERY CLAIM — PER SENTENCE
   Every sentence that makes a legal claim, states a rule, names a statute
   section, or draws a legal conclusion MUST end with at least one citation
   from the Source Registry using the EXACT label shown (e.g. [Indian Kanoon:1],
   [India Code:2]), placed immediately after the claim.
   NEVER write [1] alone — always include the source-type prefix: [Indian Kanoon:1].
   A sentence with a legal proposition but no citation is a critical failure.
   NEVER bundle as [1,2] — each citation is its own separate [Label:n] token.

3. RELEVANT CASES ONLY — USE ACTUAL JUDGMENTS
   Only cite a case if it DIRECTLY addresses the specific legal point being
   made in that sentence. Sources that are search-result pages (URL contains
   /search/ or ?formInput=) are NOT judgments — do not cite them as authority.
   Only cite sources whose URL is an actual judgment page (/doc/) or statute page.
   If no directly relevant case was retrieved, write "No directly applicable
   case law found in retrieved sources."

4. NO INVENTED CASE NAMES
   Do NOT mention any case name unless that exact case (by name) appears in the
   Source Registry above. If the registry has no case law, say so — do not
   produce case names from training data.

5. FULL TEXT OVER SNIPPETS
   Where a source is marked *(fetched)*, prefer quoting or paraphrasing from
   the fetched text. Where marked *(snippet only)*, note that this citation is
   unverified: append "(snippet only — verify independently)" after the [n].

6. ACKNOWLEDGE AMBIGUITY
   If the query could apply to multiple legal contexts (criminal vs civil,
   pre-July 2024 IPC/CrPC vs post-July 2024 BNS/BNSS, different statutes),
   open with one sentence identifying the interpretation you are answering and why.

7. NO FOREIGN LAW WITHOUT INDIAN AUTHORITY
   NEVER cite UK, US, Singapore, Australian, or any other foreign court judgment
   as authority for Indian law. If a foreign concept is relevant, write:
   "[FOREIGN LAW — no Indian authority retrieved for this point]"
   The ONLY exception: if an Indian Supreme Court judgment explicitly adopts the
   foreign principle, cite the Indian SC case — not the foreign court.

8. STRUCTURED CASE ANALYSIS — EXTRACT FACTS, ISSUE, HOLDING, RATIO
   For EVERY case cited in the Analysis section, provide ALL FOUR elements:
   - **Facts**: 1–2 sentences on the material facts of that case.
   - **Issue**: The specific legal question the court decided.
   - **Holding**: The court's actual decision — quote VERBATIM from the fetched source.
     If the full text was not fetched, write: "Holding: [NOT IN FETCHED TEXT — verify at URL]"
   - **Ratio**: The binding legal principle extracted from the holding.
   Omit a case entirely rather than providing incomplete analysis.

9. EXPLICIT CONFIDENCE MARKERS
   Prefix EVERY legal proposition in the Analysis with one of:
   - **[ESTABLISHED]** — a FETCHED primary source (statute or judgment) directly answers it.
   - **[LIKELY]** — supported only by a snippet, indirect analogy, or partial fetch.
   - **[UNCERTAIN]** — sources conflict, or the point was not clearly resolved.
   - **[NOT FOUND]** — absent from ALL retrieved sources after search.
   Example: "**[ESTABLISHED]** Section 480 BNSS governs regular bail [India Code:1]."
   Example: "**[NOT FOUND]** No Supreme Court ruling on this specific point was retrieved."

══════════════════════════════════════════
OUTPUT FORMAT — USE THIS EXACT STRUCTURE
══════════════════════════════════════════

# [Specific title — e.g. "Bail Under BNSS Section 480 After Arrest"]

**Jurisdiction:** India (Supreme Court + [relevant High Court, if identifiable])
**Applicable law:** [Primary Statute] ([year])
**Offence date:** [before / on / after 1 July 2024 — omit if not a criminal matter]

---

## Topic Snapshot

[2–3 sentences: the exact legal issue, why it matters, and the core question. No citations here.]

---

## Brief Direct Answer

Confidence: **[ESTABLISHED]** / **[LIKELY]** / **[UNCERTAIN]** (pick one — see Rule 9)

[2–3 sentences: the immediate answer. Start with the confidence marker, one-phrase reason,
and primary authority cited inline — e.g. "**[ESTABLISHED]** Yes, bail may be sought under
Section 480 BNSS [India Code:1]; the Supreme Court has held that... [Indian Kanoon:2]."]

If primary sources are missing: "**[NOT FOUND]** The retrieved sources do not contain a
fetched primary authority to answer this question. Independent research is required."

---

## Key Statutes & Authorities

| Citation Label | Authority | Status |
|---|---|---|
| [India Code:n] | [Statute Section](URL) | ✅ fetched |
| [Indian Kanoon:n] | [Case Name, Citation](URL) | ✅ fetched |
| [Indian Kanoon:n] | [Case Name](URL) | ⚠️ snippet only |

List every source from the Source Registry using the exact [Label:n] token.
✅ fetched = full judgment/statute text retrieved; ⚠️ snippet only = excerpt only.
Mark search-result pages (URL contains /search/ or ?formInput=) as ⚠️ NOT A JUDGMENT PAGE.

---

## At a Glance

| Aspect | Detail |
|---|---|
| **Governing law** | [Statute + exact section] |
| **Core issue** | [One-line description] |
| **Key consequence / penalty** | [If applicable] |
| **Applicable code** | [IPC/CrPC or BNS/BNSS — if criminal] |
| **Leading SC precedent** | [Case name + citation, or "None retrieved"] |

---

## Analysis

### [Issue or sub-question]

For EACH case cited here, provide the full structured analysis (Rule 8):

**[Case Name]** [Citation] [Indian Kanoon:n]
- **Facts**: [1–2 sentences on the material facts]
- **Issue**: [The precise legal question the court resolved]
- **Holding**: "[Verbatim quote from fetched text]" — or "[NOT IN FETCHED TEXT — verify at URL]"
- **Ratio**: [The binding principle derived from the holding]
- **Application**: [How this case's ratio applies to the research brief]

[Narrative analysis weaving together statute text and case law. Every proposition prefixed
with [ESTABLISHED] / [LIKELY] / [UNCERTAIN] / [NOT FOUND] per Rule 9. Every sentence with
a legal claim MUST end with a [Label:n] citation.]

### [Second issue, if any]

[Repeat structured analysis]

---

## Practical Takeaway

[2–3 sentences of immediately actionable guidance grounded in retrieved sources only.]

## Action Points

1. [Concrete first step — name the specific provision and procedure]
2. [Second step]
3. [Third step]

---

## Suggested Follow-up Queries

1. [Specific follow-up legal question]
2. [Specific follow-up legal question]
3. [Specific follow-up legal question]

---

*This is AI-assisted legal research; consult a lawyer for advice.*
"""

# ── Point 2: Judgment-page vs search-page detection ──────────────────────────
# A "search page" shows a list of result links — fetching it yields no judgment
# text. Only /doc/ pages on indiankanoon.org contain an actual ruling.

def _is_search_page(url: str) -> bool:
    """True when the URL is a search-results page rather than an actual judgment
    or statute document. These pages contain link lists, not legal text."""
    url_lower = (url or "").lower()
    if "indiankanoon.org" in url_lower:
        return "/search" in url_lower or "forminput=" in url_lower
    if "indiacode.nic.in" in url_lower:
        return "/search" in url_lower
    return False


# ── Point 8: Topic-specific legal checklists ─────────────────────────────────
# Injected into the answer prompt so the writer verifies topic-critical points
# that generic prompts often miss (e.g., bail triple-test, BNS transition dates).

_TOPIC_CHECKLISTS: dict[str, str] = {
    "bail": """\

TOPIC-SPECIFIC CHECKLIST — BAIL (verify ALL before finalising):
□ Applicable section: BNSS §480 (regular) / §482 (anticipatory) for post-July 2024;
  CrPC §437 / §439 / §438 for pre-July 2024 offences.
□ Bailable vs. non-bailable offence determined from First Schedule.
□ Supreme Court triple-test addressed: (1) prima facie case, (2) flight risk, (3) tampering risk.
□ At least one Supreme Court bail precedent cited from fetched sources.
□ Current statutory text fetched from indiacode.nic.in.""",

    "murder": """\

TOPIC-SPECIFIC CHECKLIST — MURDER / CULPABLE HOMICIDE:
□ Date of offence — BNS §103/§104 (from 1 July 2024) or IPC §302/§304 (before)?
□ Distinction between murder and culpable homicide not amounting to murder addressed.
□ Exception clauses examined: grave & sudden provocation, exceeding private defence, etc.
□ Sentencing range stated: death / life imprisonment / 10 years + fine.
□ At least one Supreme Court sentencing precedent cited from fetched sources.""",

    "property": """\

TOPIC-SPECIFIC CHECKLIST — PROPERTY / LAND:
□ Transfer of Property Act 1882 — relevant section fetched from indiacode.nic.in?
□ Registration Act 1908 — if title or registration is in issue?
□ Specific Relief Act 1963 — for specific performance or injunction claims?
□ Limitation period for property disputes identified (Article 65 / Article 58 Limitation Act)?
□ At least one Supreme Court property/land precedent cited from fetched sources.""",

    "contract": """\

TOPIC-SPECIFIC CHECKLIST — CONTRACT:
□ Indian Contract Act 1872 — relevant sections fetched (§10 validity, §73 damages, §74 penalty)?
□ Breach type classified: fundamental / anticipatory / partial.
□ Specific Relief Act 1963 §10 — specific performance availability addressed?
□ ICA §74 — whether pre-estimated damages clause is enforceable?
□ Limitation period: 3 years from date of breach (Article 55 Limitation Act)?""",

    "constitutional": """\

TOPIC-SPECIFIC CHECKLIST — CONSTITUTIONAL / WRIT:
□ Fundamental right invoked — exact Article and text from Constitution of India?
□ Ground of restriction under Article 19(2)–(6) or Article 21 procedure test stated?
□ Doctrine of proportionality addressed if restriction is challenged?
□ Writ jurisdiction: Article 32 (Supreme Court) or Article 226 (High Court)?
□ At least one Constitution Bench / 9-judge bench precedent cited if applicable.""",

    "criminal_procedure": """\

TOPIC-SPECIFIC CHECKLIST — CRIMINAL PROCEDURE:
□ Date of offence — BNSS (from 1 July 2024) or CrPC (before) — stated clearly?
□ Arrest grounds: §35 BNSS / §41 CrPC — satisfaction of conditions stated?
□ Remand period: §187 BNSS / §167 CrPC — 15/60/90-day limits addressed?
□ FIR registration obligation: §173 BNSS / §154 CrPC mandatory on cognisable offence?
□ Charge-sheet deadline: 60 days (non-serious) / 90 days (serious) addressed?""",
}

_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "bail": ["bail", "anticipatory bail", "custody", "remand", "detention",
             "480", "437", "438", "439", "482"],
    "murder": ["murder", "culpable homicide", "302", "103 bns", "death", "life imprisonment"],
    "property": ["property", "land", "title", "possession", "transfer", "sale deed",
                 "mortgage", "lease", "specific performance"],
    "contract": ["contract", "breach", "agreement", "specific performance",
                 "liquidated damages", "indian contract act"],
    "constitutional": ["article 21", "article 19", "article 14", "article 22",
                       "fundamental right", "writ", "habeas corpus", "constitutional"],
    "criminal_procedure": ["fir", "arrest", "charge sheet", "chargesheet", "remand",
                           "investigation", "police", "§154", "§173", "section 154",
                           "section 173"],
}


def _detect_topic(brief: str) -> str | None:
    """Return the best-matching legal topic for the research brief, or None."""
    brief_lower = (brief or "").lower()
    for topic, keywords in _TOPIC_KEYWORDS.items():
        if any(kw.lower() in brief_lower for kw in keywords):
            return topic
    return None


def _get_topic_checklist(brief: str) -> str:
    """Return the topic-specific checklist block to inject into the answer prompt."""
    topic = _detect_topic(brief)
    return _TOPIC_CHECKLISTS.get(topic, "") if topic else ""


# ── Point 7: Foreign-law post-processor ──────────────────────────────────────
# Detects references to foreign courts in the generated answer that are not
# backed by an Indian authority explicitly adopting the foreign principle.

_FOREIGN_COURT_RE = re.compile(
    r"\b("
    r"UK Supreme Court|House of Lords|Court of Appeal|[Ee]nglish (?:court|law|case)"
    r"|US Supreme Court|United States Supreme Court|American (?:court|law|case)"
    r"|Singapore Court|Singapore Court of Appeal|Singaporean"
    r"|Australian (?:High Court|court|law)"
    r"|Canadian Supreme Court|Supreme Court of Canada"
    r")\b",
    re.IGNORECASE,
)


def _flag_foreign_law(content: str, citable: list[RetrievedSource]) -> str:
    """Append a warning if the answer references foreign courts without Indian authority."""
    matches = _FOREIGN_COURT_RE.findall(content)
    if not matches:
        return content
    # Check whether any fetched Indian source explicitly mentions "adopts" or "follows"
    # the foreign principle — if so, it is legitimately grounded.
    indian_corpus = " ".join(s.excerpt for s in citable if s.fetched).lower()
    ungrounded = [
        m for m in set(matches)
        if not any(
            phrase in indian_corpus
            for phrase in ("adopted", "follows", "relied upon", "approved")
        )
    ]
    if not ungrounded:
        return content
    courts = "; ".join(set(ungrounded))
    return content + (
        "\n\n> **Foreign Law Warning**: References to foreign courts detected — "
        f"{courts}. These carry no binding authority in Indian law. "
        "Ensure each such reference is supported by an Indian Supreme Court judgment "
        "that explicitly adopted the foreign principle. If not, remove or mark as "
        "[FOREIGN LAW — no Indian authority retrieved]."
    )


# ── Citation helpers ───────────────────────────────────────────────────────────

# Matches plain [n] and source-type-qualified [Label:n] citations; group 1 = numeric index
_INLINE_CITATION_RE = re.compile(r"\[(?:[A-Za-z][A-Za-z\s]*:\s*)?(\d+)\]")


def _collect_sources(state: AgentState) -> list[RetrievedSource]:
    return [
        item if isinstance(item, RetrievedSource) else RetrievedSource(**item)
        for item in (state.get("retrieved_sources") or [])
    ]


def _validate_inline_citations(text: str, num_sources: int) -> str:
    """Replace out-of-range [n] references with [?] so hallucinated citations are visible."""
    if num_sources == 0:
        return _INLINE_CITATION_RE.sub("[?]", text)

    def _replace(match: re.Match) -> str:
        n = int(match.group(1))
        return match.group(0) if 1 <= n <= num_sources else "[?]"

    return _INLINE_CITATION_RE.sub(_replace, text)


def _append_sources_section(report: str, sources: list[RetrievedSource]) -> str:
    """Append a ## Table of Authorities section matching the new output template.

    Sources are grouped by type (Statutes → Case Law → Web) and labelled using
    the same [Label:n] tokens emitted by format_writer_source_registry, so every
    inline citation in the answer maps directly to a row in this table.
    ✅ fetched = full text was retrieved; ⚠️ snippet only = only a search excerpt.
    """
    if not sources:
        return report

    statutes: list[tuple[int, RetrievedSource]] = []
    cases: list[tuple[int, RetrievedSource]] = []
    others: list[tuple[int, RetrievedSource]] = []

    for index, src in enumerate(sources, 1):
        url = (src.url or "").strip()
        if not url.startswith("http"):
            continue
        stype = src.source_type or "web"
        if stype == "indiacode":
            statutes.append((index, src))
        elif stype in ("indiankanoon", "escr"):
            cases.append((index, src))
        else:
            others.append((index, src))

    # If all sources are ungrouped, put them in others so nothing is lost
    if not statutes and not cases:
        others = [(i, s) for i, s in enumerate(sources, 1) if (s.url or "").startswith("http")]

    def _entry(index: int, src: RetrievedSource) -> str:
        url = (src.url or "").strip()
        title = (src.title or "Source").strip()
        lbl = citation_label(src, index)  # e.g. [Indian Kanoon:1]
        link = f"[{title}]({url})" if url.startswith("http") else title
        line = f"{lbl} {link}"
        if src.citation:
            line += f" — {src.citation}"
        line += "  ✅ fetched" if src.fetched else "  ⚠️ snippet only"
        return line

    lines: list[str] = []
    if statutes:
        lines.append("**Statutes**")
        lines.extend(_entry(i, s) for i, s in statutes)
        lines.append("")
    if cases:
        lines.append("**Case Law**")
        lines.extend(_entry(i, s) for i, s in cases)
        lines.append("")
    if others:
        lines.append("**Web Sources**")
        lines.extend(_entry(i, s) for i, s in others)
        lines.append("")

    if not lines:
        return report

    # Remove any existing sources/authorities block before re-appending
    cleaned = re.sub(
        r"\n## Table of Authorities\s*[\s\S]*?(?=\n## |\Z)",
        "",
        report or "",
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\n### Sources\s*[\s\S]*?(?=\n## |\Z)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.rstrip() + "\n\n## Table of Authorities\n\n" + "\n".join(lines).rstrip()


def _mark_unverified_case_names(content: str, citable: list[RetrievedSource]) -> str:
    """Append a warning block for case names cited but not found in any source excerpt.

    Uses extract_case_names() to detect "X v Y" patterns in the LLM output, then
    checks each against the corpus of source titles, citations, and excerpts.
    Names not found in any retrieved text are flagged — they may be hallucinated.
    """
    cited_names = extract_case_names(content)
    if not cited_names:
        return content

    corpus = " ".join(
        f"{src.title} {src.citation or ''} {src.excerpt}"
        for src in citable
    ).upper()

    unverified: list[str] = []
    for name in cited_names:
        # Split on " V " (normalised by extract_case_names) and check both parties
        parts = name.split(" V ")
        if len(parts) >= 2:
            p1 = parts[0].strip()
            p2 = " V ".join(parts[1:]).strip()
            if p1 not in corpus and p2 not in corpus:
                unverified.append(name)
        elif name not in corpus:
            unverified.append(name)

    if not unverified:
        return content

    joined = "; ".join(f"*{n.title()}*" for n in unverified)
    warning = (
        "\n\n> **Citation Warning — Unverified Case Names**: The following "
        f"case name(s) do not appear in the retrieved sources and may be "
        f"hallucinated. Independently verify before relying on them: {joined}"
    )
    return content + warning


# ── Improvement 1: Statute section verification ────────────────────────────────
#
# Statute sections are high-stakes: a wrong section number (e.g. quoting IPC
# numbering for a BNS provision) is a silent factual error that survives all
# prompt-level guards. This check catches citations that name a section number
# against an [India Code:N] source but where that number is absent from the
# fetched excerpt — flagging potential hallucinations or transposition errors.

_STATUTE_SECTION_RE = re.compile(
    r"\b(?:section|§|s\.)\s*(\d+[A-Za-z]*(?:\([a-z0-9]+\))?)\b",
    re.IGNORECASE,
)

_INDIA_CODE_CITE_RE = re.compile(r"\[India\s+Code:\d+\]", re.IGNORECASE)


def _verify_statute_sections(content: str, citable: list[RetrievedSource]) -> str:
    """Append a warning for any statute section number claimed in an [India Code:N]
    sentence that cannot be found in the fetched excerpt of any India Code source."""
    indiacode_fetched = [
        s for s in citable
        if s.source_type == "indiacode" and s.fetched and s.excerpt
    ]
    if not indiacode_fetched:
        return content

    # Verified sections = section numbers that physically appear in the fetched text
    verified_corpus = " ".join(s.excerpt for s in indiacode_fetched).upper()

    unverified: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", content):
        if not _INDIA_CODE_CITE_RE.search(sentence):
            continue
        for sec_num in _STATUTE_SECTION_RE.findall(sentence):
            sec = sec_num.upper().strip()
            if sec in unverified:
                continue
            found = any(
                pat in verified_corpus
                for pat in (f"SECTION {sec}", f"§ {sec}", f"§{sec}")
            )
            if not found:
                unverified.append(sec)

    if not unverified:
        return content

    joined = ", ".join(f"§{n}" for n in unverified)
    return content + (
        "\n\n> **Statute Verification Warning**: The following section number(s) appear in "
        "India Code citations but were not confirmed in the fetched statute text — "
        f"{joined}. Verify the exact text at indiacode.nic.in before relying on these references."
    )


# ── Improvement 2: Fabricated-citation and hedging detection ──────────────────
#
# The LLM can silently hallucinate reporter citations (e.g. "2024 INSC 99") that
# look real but never appeared in any retrieved source. Separately, when several
# primary sources ARE fetched, uncertainty language ("unclear", "unsettled") in
# the answer understates the evidence — the LLM is hedging where the sources
# don't justify it. Both patterns are caught here with zero additional API calls.


def _normal_deterministic_checks(
    content: str,
    findings: str,
    citable: list[RetrievedSource],
) -> dict:
    """Detect fabricated reporter citations and unjustified hedging in the answer.

    Builds a combined corpus from the retrieved findings text plus every source
    excerpt, then checks:
    - Reporter citations (INSC, SCC, AIR …) in the answer absent from that corpus
    - Uncertainty phrases used when ≥2 primary sources were fetched
    """
    # Combine retrieved text + source excerpts so truncation doesn't cause false positives
    corpus = findings or ""
    for src in citable:
        corpus += f"\n{src.title} {src.citation or ''} {src.excerpt}"
    corpus_norm = corpus.upper()

    report_citations = extract_citations(content)
    fabricated = [c for c in report_citations if c not in corpus_norm]

    fetched_count = sum(1 for s in citable if s.fetched)
    hedged = fetched_count >= 2 and bool(
        re.search(
            r"\b("
            r"unsettled"
            r"|unclear"
            r"|ambiguous"
            r"|no (?:direct )?cases? found"
            r"|not established"
            r"|no clear (?:authority|answer|law)"
            r"|cannot be (?:definitively )?determined"
            r")\b",
            content,
            re.IGNORECASE,
        )
    )

    return {
        "passed": not fabricated and not hedged,
        "fabricated": fabricated,
        "hedged_despite_sources": hedged,
        "fetched_count": fetched_count,
    }


# ── Improvement 3: Verification caveats block ─────────────────────────────────
#
# Rather than silently masking problems, we append a visible block so the reader
# knows exactly what was flagged and must be independently verified — the same
# "fail-open but transparent" principle used in the full deep-research pipeline.


def _append_normal_caveats(content: str, det: dict) -> str:
    """Append a structured verification-notes block when deterministic checks flag issues."""
    parts: list[str] = []
    if det.get("fabricated"):
        cites = "; ".join(det["fabricated"])
        parts.append(
            "**Unverified reporter citations** (present in answer but absent from all "
            f"retrieved sources — may be hallucinated): {cites}"
        )
    if det.get("hedged_despite_sources"):
        count = det.get("fetched_count", 0)
        parts.append(
            f"**Confidence gap**: uncertainty language used despite {count} fetched "
            "primary source(s). Review the cited sources directly to form a definitive "
            "conclusion rather than relying on the hedged language above."
        )
    if not parts:
        return content
    lines = ["---", "", "**Answer Verification Notes**", ""]
    lines += [f"> - {p}" for p in parts]
    return content + "\n\n" + "\n".join(lines)


# ── Node: normal_researcher — 2-3 round search + fetch loop ───────────────────


async def normal_researcher(state: AgentState, config: RunnableConfig) -> dict:
    """Lightweight retrieval loop — 2-3 search + fetch rounds using async MCP client."""
    brief = state.get("research_brief") or ""
    if not brief:
        for msg in reversed(state.get("messages", [])):
            if getattr(msg, "type", None) == "human":
                brief = str(getattr(msg, "content", ""))
                break

    max_queries = app_config.NORMAL_MAX_SEARCH_QUERIES
    max_fetches = app_config.NORMAL_MAX_FETCHES
    results_per_query = app_config.NORMAL_RESULTS_PER_QUERY
    tenant_id = (config.get("configurable") or {}).get("tenant_id")

    # 1. Plan search queries via LLM
    planner_prompt = _PLANNER_PROMPT.format(
        max_queries=max_queries,
        brief=brief,
        date=get_today_str(),
    )
    planner_response = await ainvoke_with_retry(
        _search_model,
        [HumanMessage(content=planner_prompt)],
    )
    raw_queries = str(getattr(planner_response, "content", "") or "").strip()
    queries = [q.strip() for q in raw_queries.splitlines() if q.strip()][:max_queries]
    if not queries:
        queries = [brief[:300]]

    # 2. Execute searches and fetch top results asynchronously
    client = get_retrieval_client()
    all_snippets: list[str] = []
    sources: list[RetrievedSource] = list(_collect_sources(state))
    fetched_urls: set[str] = {s.url for s in sources if s.url}
    fetch_count = 0

    for query in queries:
        try:
            hits = await client.search(
                query=query,
                search_type="all",
                max_results=results_per_query,
                tenant_id=tenant_id,
            )
        except Exception:  # noqa: BLE001
            hits = []

        snippet_text = format_retrieval_results(hits)
        if snippet_text:
            all_snippets.append(f"[Search: {query}]\n{snippet_text}")

        new_sources = sources_from_search_hits(hits)
        sources.extend(new_sources)

        # Sort fetch candidates so the most authoritative judgment pages come first:
        # 1. Skip search-result pages — they contain link lists, not judgment text.
        # 2. Prefer Supreme Court sources (digiscr.sci.gov.in, main.sci.gov.in) over HC.
        # 3. Prefer primary tier (indiacode, indiankanoon, .gov.in) over secondary.
        def _fetch_rank(src: RetrievedSource) -> tuple:
            url_lower = (src.url or "").lower()
            is_search = 1 if _is_search_page(src.url) else 0
            # SC official sites rank above all other primary sources
            is_sc = 0 if any(d in url_lower for d in (
                "digiscr.sci.gov.in", "main.sci.gov.in", "sci.gov.in"
            )) else 1
            tier_rank = {"primary": 0, "secondary": 1, "unknown": 2}.get(
                src.authority_tier, 2
            )
            return (is_search, is_sc, tier_rank)

        fetch_candidates = sorted(new_sources, key=_fetch_rank)

        # Fetch the top 2 prioritised URLs per query (up to max_fetches total)
        for src in fetch_candidates[:2]:
            if fetch_count >= max_fetches:
                break
            url = src.url or ""
            if not url or url in fetched_urls:
                continue
            fetched_urls.add(url)
            fetch_count += 1
            try:
                data = await client.fetch(url=url)
                full_text = format_fetch_result(data, url)
                if full_text:
                    all_snippets.append(f"[Fetched: {url}]\n{full_text[:3000]}")
                fetched_src = source_from_fetch(url, data, app_config.FETCH_MAX_CHARS)
                if fetched_src is not None:
                    fetched_src.fetched = True
                    sources.append(fetched_src)
            except Exception:  # noqa: BLE001
                pass

    findings = "\n\n".join(all_snippets) if all_snippets else "No sources retrieved."

    # Deduplicate by normalised URL so each source appears exactly once
    sources = merge_retrieved_sources(sources, [])

    return {
        "notes": [findings],
        "raw_notes": [findings],
        "retrieved_sources": sources,
    }


# ── Node: generate_normal_answer ───────────────────────────────────────────────


async def generate_normal_answer(state: AgentState, config: RunnableConfig) -> dict:
    """Draft a concise answer from normal research findings."""
    brief = state.get("research_brief") or ""
    notes = state.get("notes") or []
    findings = "\n\n".join(notes) if notes else "No findings."

    # Trim findings to a reasonable context budget
    char_budget = app_config.NORMAL_FINDINGS_CHAR_BUDGET
    if len(findings) > char_budget:
        findings = findings[:char_budget] + "\n\n[Findings truncated for brevity.]"

    # Deduplicate once more (state reducer may have introduced duplicates) and
    # build the ordered citable list.  This SAME ordering defines [n] numbers —
    # we use it for both the LLM prompt and the appended ### Sources section.
    merged = merge_retrieved_sources(_collect_sources(state), [])
    citable = filter_citable_sources(merged)

    # Count how many sources have full text fetched (not just snippets)
    fetched_count = sum(1 for s in citable if s.fetched)

    # Hard stop 1 — no sources at all / all searches returned errors.
    all_searches_empty = all(
        "No valid search results" in n or "No sources retrieved" in n or "Search failed" in n
        for n in (notes or ["No sources retrieved"])
    )
    if not citable or (fetched_count == 0 and all_searches_empty):
        content = (
            "**Research Incomplete — Insufficient Sources Retrieved.**\n\n"
            "The retrieval system did not return usable legal sources for this query. "
            f"Sources found: {len(citable)} (0 with full text). "
            "Generating an answer without verified sources would risk producing hallucinated "
            "case names, wrong section numbers, and fabricated statutes.\n\n"
            "**What to do:**\n"
            "1. Verify the retrieval server is running (`RETRIEVAL_SERVER_URL`).\n"
            "2. Set `TAVILY_API_KEY` or `INDIANKANOON_API_KEY` for reliable Indian legal search.\n"
            "3. Try a more specific query (Act name + section number).\n"
            "4. Retry — DuckDuckGo rate-limiting can cause transient failures.\n\n"
            "*This is AI-assisted legal research; consult a lawyer for advice.*"
        )
        return {"final_report": content, "messages": [AIMessage(content=content)]}

    # Hard stop 2 (Point 6) — refuse when NO primary-tier sources were fetched.
    # Secondary sources (blogs, commentary) cannot verify statute text or case holdings.
    primary_fetched = [
        s for s in citable if s.fetched and s.authority_tier == "primary"
    ]
    if not primary_fetched:
        snippet_count = sum(1 for s in citable if not s.fetched)
        content = (
            "**Research Incomplete — No Primary Legal Sources Retrieved.**\n\n"
            f"Sources found: {len(citable)} ({fetched_count} fetched, {snippet_count} snippet-only), "
            "but **none** are from authoritative primary domains "
            "(indiankanoon.org, indiacode.nic.in, digiscr.sci.gov.in, or official .gov.in courts).\n\n"
            "Generating an answer from secondary sources only risks producing unverified statutory "
            "text, incorrect section numbers, and hallucinated case holdings.\n\n"
            "**What to do:**\n"
            "1. Retry with a more specific query — include the Act name and section number.\n"
            "2. Try: `site:indiacode.nic.in \"[Act Name]\" section [N]`\n"
            "3. Try: `site:indiankanoon.org [legal issue] supreme court [year]`\n"
            "4. Ensure `INDIANKANOON_API_KEY` or `TAVILY_API_KEY` is configured.\n\n"
            "*This is AI-assisted legal research; consult a lawyer for advice.*"
        )
        return {"final_report": content, "messages": [AIMessage(content=content)]}

    source_registry = format_writer_source_registry(citable)

    # Build source adequacy warning appended to the prompt when retrieval is thin.
    # This prevents the LLM from filling gaps from training memory.
    adequacy_warning = ""
    min_required = app_config.NORMAL_MIN_FETCHED_SOURCES
    if fetched_count < min_required:
        adequacy_warning = (
            f"\n\n⚠️ SOURCE ADEQUACY WARNING: Only {fetched_count} of {len(citable)} sources "
            f"have full text fetched (minimum recommended: {min_required}). "
            "The remaining sources are search-snippet only. "
            "For EVERY legal claim where you do not have a fetched source: "
            "write explicitly 'NOT FOUND in retrieved sources — independent verification required.' "
            "DO NOT fill any gap from training data memory. "
            "A shorter honest answer is better than a longer hallucinated one."
        )

    # Inject the topic-specific checklist (Point 8) so the writer verifies
    # topic-critical points the generic prompt cannot anticipate.
    topic_checklist = _get_topic_checklist(brief)

    prompt = _ANSWER_PROMPT.format(
        brief=brief,
        findings=findings,
        source_registry=source_registry,
        date=get_today_str(),
        topic_checklist=topic_checklist,
    ) + adequacy_warning

    try:
        result = await ainvoke_with_retry(
            _answer_model,
            [HumanMessage(content=prompt)],
        )
        content = str(getattr(result, "content", "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        content = (
            "# Legal Research Answer\n\n"
            f"Could not generate an answer due to an error: {exc}.\n\n"
            "Please retry your question."
        )

    # Guard against hallucinated citation numbers (e.g. [5] when only 3 sources exist)
    content = _validate_inline_citations(content, len(citable))

    # Priority 6 — flag any case names cited that don't appear in retrieved text
    content = _mark_unverified_case_names(content, citable)

    # Improvement 1 — statute section cross-check: verify that every section number
    # appearing in an [India Code:N] sentence is present in the fetched source text.
    content = _verify_statute_sections(content, citable)

    # Point 7 — foreign law guard: flag references to foreign courts not backed by
    # an Indian authority that explicitly adopted the foreign principle.
    content = _flag_foreign_law(content, citable)

    # Improvement 2+3 — fabricated reporter-citation detection and hedging check.
    # Both are deterministic (no extra API call) and run on the already-generated text.
    det = _normal_deterministic_checks(content, findings, citable)
    if not det["passed"]:
        content = _append_normal_caveats(content, det)

    # Append a structured ### Sources section using the same [n] numbering as the
    # registry the LLM was given — so inline refs and footer entries always align.
    content = _append_sources_section(content, citable)

    return {
        "final_report": content,
        "messages": [AIMessage(content=content)],
    }


# ── Graph construction ──────────────────────────────────────────────────────────

normal_researcher_builder = StateGraph(AgentState, input_schema=AgentInputState)

normal_researcher_builder.add_node("load_memory", load_memory)
normal_researcher_builder.add_node("compact_conversation", compact_conversation)
normal_researcher_builder.add_node("write_research_brief", write_research_brief)
normal_researcher_builder.add_node("normal_researcher", normal_researcher)
normal_researcher_builder.add_node("generate_normal_answer", generate_normal_answer)

normal_researcher_builder.add_edge(START, "load_memory")
normal_researcher_builder.add_edge("load_memory", "compact_conversation")
normal_researcher_builder.add_edge("compact_conversation", "write_research_brief")
normal_researcher_builder.add_edge("write_research_brief", "normal_researcher")
normal_researcher_builder.add_edge("normal_researcher", "generate_normal_answer")
normal_researcher_builder.add_edge("generate_normal_answer", END)
