
"""Full Multi-Agent Research System

This module integrates all components of the research system:
- User clarification and scoping
- Research brief generation  
- Multi-agent research coordination
- Final report generation

The system orchestrates the complete research workflow from initial user
input through final report delivery.
"""

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

# ===== Config =====
from deep_research_from_scratch.config import config as app_config
from deep_research_from_scratch.model_config import (
    ainvoke_with_retry,
    fit_writer_prompt,
    get_chat_model,
    is_rate_limit_error,
)
from deep_research_from_scratch.multi_agent_supervisor import supervisor_agent
from deep_research_from_scratch.prompts import final_report_generation_prompt
from deep_research_from_scratch.report_verification import (
    finalize_report,
    route_after_verify,
    sanitize_report,
    verify_report,
)
from deep_research_from_scratch.research_agent_scope import (
    clarify_with_user,
    compact_conversation,
    load_memory,
    write_research_brief,
)
from deep_research_from_scratch.research_bootstrap import bootstrap_legal_research
from deep_research_from_scratch.retrieval_bridge import run_fetch, run_search
from deep_research_from_scratch.report_sources import (
    build_case_digest,
    build_procedural_timeline_digest,
)
from deep_research_from_scratch.source_enrichment import enrich_retrieved_sources
from deep_research_from_scratch.source_registry import (
    RetrievedSource,
    build_verification_corpus,
    count_fetches,
    filter_citable_sources,
    format_writer_source_registry,
)
from deep_research_from_scratch.validation.pipeline import (
    extract_evidence_node,
    format_evidence_for_state,
    route_after_coverage,
    run_pre_write_validation,
)
from deep_research_from_scratch.state_scope import AgentInputState, AgentState
from deep_research_from_scratch.status_stream import (
    astream_with_progress,
    emit_think_step,
    group_end,
    group_start,
)
from deep_research_from_scratch.utils import get_today_str
from typing_extensions import Literal

import asyncio

_writer_model = None


def _get_writer_model():
    global _writer_model
    if _writer_model is None:
        _writer_model = get_chat_model("writer", max_tokens=app_config.DEEP_WRITER_MAX_TOKENS)
    return _writer_model

# ===== FINAL REPORT GENERATION =====


def _collect_sources(state: AgentState) -> list[RetrievedSource]:
    sources: list[RetrievedSource] = []
    for item in state.get("retrieved_sources") or []:
        sources.append(item if isinstance(item, RetrievedSource) else RetrievedSource(**item))
    return sources


def _trim_findings(findings: str, char_budget: int) -> str:
    if len(findings) <= char_budget:
        return findings
    return (
        findings[:char_budget]
        + "\n\n[Findings truncated — cite ONLY from the Permitted Source Registry below.]"
    )

# ===== BOOTSTRAP RESEARCH (deterministic, no LLM) =====


async def bootstrap_research(state: AgentState, config: RunnableConfig):
    """Pre-fetch primary sources via retrieval MCP before the LLM supervisor runs."""
    user_query = ""
    for message in reversed(state.get("messages", []) or []):
        if getattr(message, "type", None) == "human":
            user_query = str(getattr(message, "content", "") or "")
            break

    brief = state.get("research_brief") or ""
    note, raw, sources = await asyncio.to_thread(
        bootstrap_legal_research, brief, user_query
    )
    if not note:
        return {}

    return {
        "notes": [note],
        "raw_notes": [raw],
        "retrieved_sources": sources,
    }


def route_after_bootstrap(
    state: AgentState,
) -> Literal["supervisor_subgraph", "enrich_sources"]:
    """Skip the LLM supervisor when fast mode has enough fetched primary sources."""
    if not app_config.DEEP_FAST_RESEARCH_MODE:
        return "supervisor_subgraph"
    sources = _collect_sources(state)
    _, primary_fetches = count_fetches(sources)
    if primary_fetches >= app_config.DEEP_BOOTSTRAP_MIN_TARGET_FETCHES:
        return "enrich_sources"
    return "supervisor_subgraph"


async def run_supervisor_subgraph(state: AgentState, config: RunnableConfig) -> dict:
    """Run the multi-agent supervisor with live progress forwarded to the UI."""
    group_start("analyze", "Analyzing findings", "analyze")
    emit_think_step("analyze", "Coordinating multi-agent research")

    supervisor_input = {
        "supervisor_messages": list(state.get("supervisor_messages") or []),
        "research_brief": state.get("research_brief") or "",
        "notes": list(state.get("notes") or []),
        "research_iterations": state.get("research_iterations", 0) or 0,
        "raw_notes": list(state.get("raw_notes") or []),
        "retrieved_sources": list(state.get("retrieved_sources") or []),
    }

    result = await astream_with_progress(supervisor_agent, supervisor_input, config=config)

    group_end("analyze", "Analysis complete")

    update: dict = {}
    for key in (
        "supervisor_messages",
        "notes",
        "raw_notes",
        "retrieved_sources",
        "research_iterations",
        "research_brief",
    ):
        if key in result and result[key] is not None:
            update[key] = result[key]
    return update


# ===== SOURCE ENRICHMENT (re-fetch snippets + targeted searches) =====


async def enrich_sources(state: AgentState, config: RunnableConfig):
    """Re-fetch snippet sources and run targeted BNS/chargesheet/procedural searches."""
    sources = _collect_sources(state)
    if not sources:
        return {}

    user_query = ""
    for message in reversed(state.get("messages", []) or []):
        if getattr(message, "type", None) == "human":
            user_query = str(getattr(message, "content", "") or "")
            break

    enriched, note = await asyncio.to_thread(
        enrich_retrieved_sources,
        sources,
        research_brief=state.get("research_brief") or "",
        user_query=user_query,
    )
    update: dict = {"retrieved_sources": enriched}
    if note:
        notes = list(state.get("notes") or [])
        notes.append(note)
        update["notes"] = notes
    return update


# ===== SOURCE VALIDATION (Phases 1 + 4 + 6) =====


async def validate_and_score_sources(state: AgentState, config: RunnableConfig):
    """Validate and score retrieved sources before report generation."""
    return run_pre_write_validation(state)


async def targeted_gap_research(state: AgentState, config: RunnableConfig):
    """Run targeted searches to fill coverage gaps."""
    queries = state.get("coverage_gap_queries") or []
    if not queries:
        return {"gap_research_retries": state.get("gap_research_retries", 0) + 1}

    new_sources: list[RetrievedSource] = []
    raw_notes: list[str] = []
    for query in queries[:2]:
        _, sources = await asyncio.to_thread(
            run_search, query, app_config.DEEP_BOOTSTRAP_RESULTS_PER_QUERY
        )
        for src in sources[:2]:
            if src.url:
                _, fetched = await asyncio.to_thread(run_fetch, src.url)
                if fetched:
                    new_sources.append(fetched)
                    raw_notes.append(f"Gap research fetch: {fetched.title}")

    update: dict = {
        "gap_research_retries": state.get("gap_research_retries", 0) + 1,
        "coverage_gap_queries": [],
    }
    if new_sources:
        update["retrieved_sources"] = new_sources
    if raw_notes:
        update["raw_notes"] = raw_notes
    return update


async def extract_evidence(state: AgentState, config: RunnableConfig):
    """Build evidence pack from validated sources."""
    return extract_evidence_node(state)


# ===== FINAL REPORT GENERATION =====


async def _invoke_writer(
    prompt: str,
    *,
    findings: str,
    safe_max_tokens: int | None,
) -> str:
    """Call the writer LLM with 429 backoff and a lighter fallback pass."""
    # (findings_char_budget, max_tokens, cooldown_seconds, max_retries)
    attempts: list[tuple[int, int | None, float, int]] = [
        (app_config.DEEP_LLM_FINDINGS_CHAR_BUDGET, safe_max_tokens, 0.0, 8),
        (40_000, safe_max_tokens, 20.0, 5),
        (24_000, 4096, 35.0, 4),
    ]
    last_exc: Exception | None = None

    for idx, (char_budget, max_tokens, cooldown, retries) in enumerate(attempts):
        if cooldown > 0:
            await asyncio.sleep(cooldown)

        candidate_prompt = prompt
        if idx > 0:
            trimmed = _trim_findings(findings, char_budget)
            candidate_prompt = prompt.replace(findings, trimmed, 1)

        bound_model = (
            _get_writer_model().bind(max_tokens=max_tokens)
            if max_tokens is not None
            else _get_writer_model()
        )
        try:
            result = await ainvoke_with_retry(
                bound_model,
                [HumanMessage(content=candidate_prompt)],
                max_retries=retries,
            )
            content = str(getattr(result, "content", "") or "").strip()
            if content:
                return content
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not is_rate_limit_error(exc) or idx >= len(attempts) - 1:
                raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Writer returned empty content after all attempts.")


async def final_report_generation(state: AgentState, config: RunnableConfig):
    """Final report generation node.

    Drafts the memorandum from the research findings. On a revision pass it
    incorporates the verification reviewer's feedback. Delivery + persistence
    happen in ``finalize_report`` (after verification), so this node only
    produces the draft.
    """
    notes = state.get("notes", [])
    sources = filter_citable_sources(_collect_sources(state))
    findings_text = "\n".join(notes)
    if not findings_text.strip() and sources:
        findings_text = build_verification_corpus(
            [], state.get("raw_notes", []), sources
        )
    findings = _trim_findings(findings_text, app_config.DEEP_LLM_FINDINGS_CHAR_BUDGET)
    source_registry = format_writer_source_registry(sources)
    case_digest = build_case_digest(sources)
    timeline_digest = build_procedural_timeline_digest(
        sources, state.get("research_brief") or ""
    )

    # On a revise pass, feed the reviewer's required fixes back to the writer.
    verification = state.get("verification")
    if verification is not None and verification.required_fixes:
        verification_feedback = verification.required_fixes
    else:
        verification_feedback = "This is the first draft - no reviewer feedback yet."

    final_report_prompt = final_report_generation_prompt.format(
        research_brief=state.get("research_brief", ""),
        findings=findings,
        source_registry=source_registry,
        case_digest=case_digest,
        timeline_digest=timeline_digest,
        evidence_pack=format_evidence_for_state(state),
        date=get_today_str(),
        verification_feedback=verification_feedback,
    )

    final_report_prompt, safe_max_tokens = fit_writer_prompt(
        final_report_prompt,
        findings=findings,
        trim_findings=_trim_findings,
        requested_max_tokens=app_config.DEEP_WRITER_MAX_TOKENS,
        min_completion_tokens=app_config.DEEP_MIN_WRITER_COMPLETION_TOKENS,
    )

    group_start("write", "Writing legal memorandum", "write")
    emit_think_step("write", f"Structuring memo from {len(sources)} sources")

    try:
        content = await _invoke_writer(
            final_report_prompt,
            findings=findings,
            safe_max_tokens=safe_max_tokens,
        )
    except Exception as e:  # noqa: BLE001 - degrade gracefully; verification will flag/caveat it
        if is_rate_limit_error(e):
            content = (
                "# Legal Research Memorandum\n\n"
                "**Mistral API rate limit reached.** The research sources were "
                "retrieved successfully, but the memorandum could not be written "
                "after several retries.\n\n"
                "Please wait 1–2 minutes and submit the same query again — your "
                "session may continue from cached research. "
                "No legal conclusions should be drawn from this message."
            )
        else:
            content = (
                "# Legal Research Memorandum\n\n"
                f"The memorandum could not be generated due to an error: {e}. "
                "Please retry. No legal conclusions should be drawn from this message."
            )

    group_end("write", "Memorandum complete")

    return {"final_report": content}

# ===== GRAPH CONSTRUCTION =====
# Build the overall workflow
deep_researcher_builder = StateGraph(AgentState, input_schema=AgentInputState)

# Add workflow nodes
deep_researcher_builder.add_node("load_memory", load_memory)
deep_researcher_builder.add_node("compact_conversation", compact_conversation)
deep_researcher_builder.add_node("clarify_with_user", clarify_with_user)
deep_researcher_builder.add_node("write_research_brief", write_research_brief)
deep_researcher_builder.add_node("bootstrap_research", bootstrap_research)
deep_researcher_builder.add_node("supervisor_subgraph", run_supervisor_subgraph)
deep_researcher_builder.add_node("enrich_sources", enrich_sources)
deep_researcher_builder.add_node("validate_and_score_sources", validate_and_score_sources)
deep_researcher_builder.add_node("targeted_gap_research", targeted_gap_research)
deep_researcher_builder.add_node("extract_evidence", extract_evidence)
deep_researcher_builder.add_node("final_report_generation", final_report_generation)
deep_researcher_builder.add_node("verify_report", verify_report)
deep_researcher_builder.add_node("sanitize_report", sanitize_report)
deep_researcher_builder.add_node("finalize_report", finalize_report)

# Add workflow edges
# START -> load_memory (inject long-term + conversation memory, persist turn)
# -> clarify_with_user -> write_research_brief -> supervisor -> draft report
# -> verify_report -> (revise via final_report_generation | finalize_report) -> END.
deep_researcher_builder.add_edge(START, "load_memory")
deep_researcher_builder.add_edge("load_memory", "compact_conversation")
deep_researcher_builder.add_edge("compact_conversation", "clarify_with_user")
deep_researcher_builder.add_edge("write_research_brief", "bootstrap_research")
deep_researcher_builder.add_conditional_edges(
    "bootstrap_research",
    route_after_bootstrap,
    {
        "supervisor_subgraph": "supervisor_subgraph",
        "enrich_sources": "enrich_sources",
    },
)
deep_researcher_builder.add_edge("supervisor_subgraph", "enrich_sources")
deep_researcher_builder.add_edge("enrich_sources", "validate_and_score_sources")
deep_researcher_builder.add_conditional_edges(
    "validate_and_score_sources",
    route_after_coverage,
    {
        "targeted_gap_research": "targeted_gap_research",
        "extract_evidence": "extract_evidence",
    },
)
deep_researcher_builder.add_edge("targeted_gap_research", "validate_and_score_sources")
deep_researcher_builder.add_edge("extract_evidence", "final_report_generation")
deep_researcher_builder.add_edge("final_report_generation", "verify_report")
deep_researcher_builder.add_conditional_edges(
    "verify_report",
    route_after_verify,
    {
        "final_report_generation": "final_report_generation",
        "sanitize_report": "sanitize_report",
    },
)
deep_researcher_builder.add_edge("sanitize_report", "finalize_report")
deep_researcher_builder.add_edge("finalize_report", END)

