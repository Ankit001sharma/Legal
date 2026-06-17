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
1. STATUTORY TEXT query — fetch the actual statute from indiacode.nic.in
   Format: site:indiacode.nic.in "[Full Act Name]" [section keyword]
   Example: site:indiacode.nic.in "Bharatiya Nyaya Sanhita" section 61 conspiracy
2. SUPREME COURT case law query on indiankanoon.org
   Format: site:indiankanoon.org [legal issue] supreme court
   Example: site:indiankanoon.org NEET paper leak 2024 Supreme Court
3. RECENT HIGH COURT query — post-2022 judgments
   Format: site:indiankanoon.org [issue] high court 2023 2024
4. BROADER STATUTE search — adjacent Acts that may also apply
   Example: site:indiacode.nic.in "Public Examinations" "Unfair Means" 2024
5. DIRECT INDIANKANOON search — without site: restriction for reliability fallback
   Format: [legal issue] India judgment [relevant year]

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

3. RELEVANT CASES ONLY
   Only cite a case if it DIRECTLY addresses the specific legal point being
   made in that sentence. Do NOT cite a case because it is tangentially related.
   If no directly relevant case was retrieved, write "No directly applicable
   case law found in retrieved sources." Do NOT substitute a loosely related case.

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

[2–3 sentences: the immediate answer with confidence level — **Clearly established** [n] / **Likely** [n] / **Unclear** — followed by one-phrase reason and primary authority cited inline.]

---

## Key Statutes & Authorities

| Citation Label | Authority | Status |
|---|---|---|
| [India Code:n] | [Statute Section](URL) | ✅ fetched |
| [Indian Kanoon:n] | [Case Name, Citation](URL) | ✅ fetched |
| [Indian Kanoon:n] | [Case Name](URL) | ⚠️ snippet only |

List every source from the Source Registry using the exact [Label:n] token. ✅ fetched = full text retrieved; ⚠️ snippet only = excerpt only.

---

## At a Glance

| Aspect | Detail |
|---|---|
| **Governing law** | [Statute + section] |
| **Core issue** | [One-line description] |
| **Key consequence / penalty** | [If applicable] |
| **Applicable code** | [IPC/CrPC or BNS/BNSS — if criminal] |

---

## Analysis

### [Issue or sub-question]

[2–4 paragraphs of analysis. Every sentence with a legal claim, statute reference, or case holding
MUST end with a [Label:n] citation using the exact label from the Source Registry (e.g. [India Code:1],
[Indian Kanoon:2]). Quote key holdings verbatim from fetched sources. Only cite cases whose facts
directly support the specific point being made in that sentence.]

### [Second issue, if any]

[Repeat]

---

## Practical Takeaway

[2–3 sentences of immediately actionable guidance grounded in retrieved sources.]

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

        # Prioritise primary-tier sources for full-text fetching (priority 4):
        # fetching primary authority (indiacode, indiankanoon, .gov.in) gives
        # verified text that the LLM can cite confidently; secondary/unknown
        # sources are fetched only when the primary slot budget allows.
        _tier_rank = {"primary": 0, "secondary": 1, "unknown": 2}
        fetch_candidates = sorted(
            new_sources, key=lambda s: _tier_rank.get(s.authority_tier, 2)
        )

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

    # Priority 1 — TOOLS FIRST: hard stop when retrieval has completely failed.
    # Detects both "no sources at all" and "search returned error strings only".
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

    prompt = _ANSWER_PROMPT.format(
        brief=brief,
        findings=findings,
        source_registry=source_registry,
        date=get_today_str(),
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
