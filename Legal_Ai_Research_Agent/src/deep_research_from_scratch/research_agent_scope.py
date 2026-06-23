
"""User Clarification and Research Brief Generation.

This module implements the scoping phase of the research workflow, where we:
1. Assess if the user's request needs clarification
2. Generate a detailed research brief from the conversation

The workflow uses structured output to make deterministic decisions about
whether sufficient context exists to proceed with research.
"""

import re

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    get_buffer_string,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from typing_extensions import Literal

from deep_research_from_scratch.config import config as app_config
from deep_research_from_scratch.memory_namespace import apply_config_namespace
from deep_research_from_scratch.memory_backend import format_hits, get_memory_backend
from deep_research_from_scratch.memory_tools import (
    RECALL_EXCLUDE_RECENT,
    build_session_context,
    compact_conversation,
    get_session_id,
    load_memory_prompt,
    recall_older_session_turns,
    record_transcript,
)
from deep_research_from_scratch.model_config import get_chat_model, invoke_with_retry
from deep_research_from_scratch.retrieval_bridge import set_request_context
from deep_research_from_scratch.prompts import (
    suggest_directions_prompt,
    transform_messages_into_research_topic_prompt,
)
from deep_research_from_scratch.state_scope import (
    AgentInputState,
    AgentState,
    ResearchQuestion,
    SuggestDirections,
)
from deep_research_from_scratch.status_stream import (
    emit_think_step,
    group_end,
    group_start,
)
from deep_research_from_scratch.utils import get_today_str

# Marker prefix identifying the memory block this node injects, so stale blocks
# can be removed (deduped) on subsequent turns instead of accumulating.
MEMORY_BLOCK_PREFIX = "## Persistent memory (for your awareness)"

# ===== CONFIGURATION =====

# Lazily initialized — deferred until first use so the module can be imported
# without OPENAI_API_KEY present at container startup.
_model = None


def _get_model():
    global _model
    if _model is None:
        _model = get_chat_model("reasoning", temperature=0.0)
    return _model


_GREETING_PATTERN = re.compile(
    r"^\s*(?:"
    r"hi(?:\s+there)?|"
    r"hello(?:\s+there)?|"
    r"hey(?:\s+there)?|"
    r"howdy|"
    r"good\s+(?:morning|afternoon|evening|night|day)|"
    r"what(?:'s|\s+is)\s+up|"
    r"how\s+are\s+you(?:\s+doing)?|"
    r"how\s+do\s+you\s+do|"
    r"thanks?(?:\s+you)?|"
    r"thank\s+you|"
    r"bye|"
    r"goodbye|"
    r"see\s+ya|"
    r"g(?:ood)?\s*night"
    r")"
    r"(?:\s+[!?.…]+)?\s*$",
    re.IGNORECASE,
)

_META_PATTERN = re.compile(
    r"^\s*(?:"
    r"who\s+are\s+you|"
    r"what\s+are\s+you|"
    r"what\s+can\s+you\s+do|"
    r"what\s+do\s+you\s+do"
    r")(?:\s+[!?.…]+)?\s*$",
    re.IGNORECASE,
)

_DEFAULT_GREETING_REPLY = (
    "Hello! I'm your Indian legal research assistant. "
    "I can help you research statutes, case law, legal procedures, and draft memos. "
    "What legal question would you like me to research?"
)


def _latest_user_text(state: AgentState) -> str:
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
            return msg.content.strip()
    return ""


def _is_greeting_or_meta(text: str) -> bool:
    if not text:
        return False
    return bool(_GREETING_PATTERN.match(text) or _META_PATTERN.match(text))


def _direct_response_command(text: str, session_id: str, config: RunnableConfig) -> Command:
    record_transcript(session_id, "assistant", text, config=config)
    return Command(
        goto=END,
        update={
            "messages": [AIMessage(content=text)],
            "final_report": text,
            "research_directions": [],
        },
    )

# ===== WORKFLOW NODES =====

def load_memory(state: AgentState, config: RunnableConfig) -> dict:
    """Inject memory at the start of the turn (the QueryEngine.ts / loadMemoryPrompt step).

    Hybrid recall designed for continuous, long conversations without losing
    context (and bounded in size, so it stays fast):
    1. Long-term memory index (MEMORY.md) + cross-session facts relevant to the
       request, via the pluggable memory backend (keyword now, vector-ready).
    2. A rolling per-session summary of older turns + the most recent turns
       verbatim (full transcript stays on disk; injection size is capped).
    3. Other relevant earlier turns retrieved by the query (catches context
       outside the recent window).
    4. Persists the latest message to the transcript and dedupes any stale memory
       block from a prior turn before injecting the fresh one.
    """
    session_id = get_session_id(config)
    tenant_id = (config.get("configurable") or {}).get("tenant_id")
    user_id = (config.get("configurable") or {}).get("user_id")
    role = (config.get("configurable") or {}).get("role")
    auth_token = (config.get("configurable") or {}).get("auth_token")
    apply_config_namespace(config)
    set_request_context(
        tenant_id=tenant_id,
        user_id=user_id,
        role=role,
        auth_token=auth_token,
    )
    messages = state.get("messages", [])

    # The latest user message drives retrieval (built BEFORE we record it, so it
    # is not duplicated inside the recalled conversation context).
    latest_user_text = ""
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
            latest_user_text = msg.content
            break

    # Input validation: cap pathologically long input before it drives retrieval
    # / summarization, so a single huge message cannot blow up cost or context.
    if latest_user_text and len(latest_user_text) > app_config.MAX_INPUT_CHARS:
        latest_user_text = latest_user_text[: app_config.MAX_INPUT_CHARS]

    backend = get_memory_backend()

    # 1. Long-term memory: index + cross-session facts relevant to this request
    memory_index = load_memory_prompt(config)
    longterm_hits = backend.search_longterm(latest_user_text, k=5) if latest_user_text else []
    recalled_longterm = format_hits(longterm_hits, empty="No long-term memories matched this request.")

    # 2. Bounded conversation context: recent turns first + rolled-up summary
    session_ctx = build_session_context(session_id, config=config)

    # 3. Older session turns outside the recent window (recency-weighted recall)
    if latest_user_text:
        relevant_older = recall_older_session_turns(
            session_id,
            latest_user_text,
            exclude_recent=RECALL_EXCLUDE_RECENT,
        )
    else:
        relevant_older = "None."

    memory_block = (
        f"{MEMORY_BLOCK_PREFIX}\n"
        f"{memory_index}\n\n"
        f"### CURRENT CONVERSATION (last {RECALL_EXCLUDE_RECENT} messages — HIGHEST PRIORITY)\n"
        f"{session_ctx}\n\n"
        f"### Recalled long-term facts relevant to this request\n{recalled_longterm}\n\n"
        f"### Similar earlier messages (ranked by relevance + recency)\n{relevant_older}"
    )

    # Now persist the newest message to the transcript (after building context).
    if messages:
        last = messages[-1]
        content = getattr(last, "content", "")
        if isinstance(content, str) and content.strip():
            role = "user" if isinstance(last, HumanMessage) else "assistant"
            record_transcript(session_id, role, content, config=config)

    # Dedupe: drop any stale memory block injected on a previous turn so the
    # context does not accumulate duplicate memory blocks over a long thread.
    removals = [
        RemoveMessage(id=m.id)
        for m in messages
        if isinstance(m, SystemMessage)
        and isinstance(getattr(m, "content", None), str)
        and m.content.startswith(MEMORY_BLOCK_PREFIX)
        and getattr(m, "id", None) is not None
    ]

    return {"messages": [*removals, SystemMessage(content=memory_block)]}

def clarify_with_user(state: AgentState, config: RunnableConfig) -> Command[Literal["write_research_brief", "__end__"]]:
    """Suggest research directions or ask a targeted clarifying question before starting research.

    Four possible actions:
    - direct_response: greeting/meta/off-topic — reply and stop (no research).
    - suggest_directions: present 3-4 research angles for user to choose; graph pauses.
    - ask_clarification: ask ONE missing-fact question; graph pauses.
    - proceed: start research immediately.
    """
    session_id = get_session_id(config)
    latest_user_text = _latest_user_text(state)

    if _is_greeting_or_meta(latest_user_text):
        return _direct_response_command(_DEFAULT_GREETING_REPLY, session_id, config)

    if not app_config.ALLOW_CLARIFICATION:
        return Command(
            goto="write_research_brief",
            update={
                "messages": [AIMessage(content="Proceeding with research based on the information provided.")],
                "research_directions": [],
            },
        )

    research_mode = (config.get("configurable") or {}).get("research_mode", "normal")
    if research_mode == "deep":
        return Command(
            goto="write_research_brief",
            update={
                "messages": [
                    AIMessage(
                        content="Starting deep legal research based on your query."
                    )
                ],
                "research_directions": [],
            },
        )

    structured_output_model = _get_model().with_structured_output(SuggestDirections)

    response = structured_output_model.invoke([
        HumanMessage(content=suggest_directions_prompt.format(
            messages=get_buffer_string(messages=state["messages"]),
            date=get_today_str(),
            current_query=latest_user_text or "(see conversation history)",
        ))
    ])

    if response.action == "direct_response":
        text = (response.direct_response or _DEFAULT_GREETING_REPLY).strip()
        return _direct_response_command(text, session_id, config)

    if response.action == "suggest_directions":
        directions_list = "\n".join(
            f"{i + 1}. {d}" for i, d in enumerate(response.research_directions)
        )
        text = f"{response.direction_context}\n\n{directions_list}"
        record_transcript(session_id, "assistant", text, config=config)
        return Command(
            goto=END,
            update={
                "messages": [AIMessage(content=text)],
                "research_directions": response.research_directions,
            },
        )

    if response.action == "ask_clarification":
        record_transcript(session_id, "assistant", response.clarification_question, config=config)
        return Command(
            goto=END,
            update={
                "messages": [AIMessage(content=response.clarification_question)],
                "research_directions": [],
            },
        )

    # action == "proceed"
    record_transcript(session_id, "assistant", response.verification, config=config)
    return Command(
        goto="write_research_brief",
        update={
            "messages": [AIMessage(content=response.verification)],
            "research_directions": [],
        },
    )

def write_research_brief(state: AgentState, config: RunnableConfig):
    """Transform the conversation history into a comprehensive research brief.

    Uses structured output to ensure the brief follows the required format
    and contains all necessary details for effective research.
    """
    # Set up structured output model
    structured_output_model = _get_model().with_structured_output(ResearchQuestion)

    # Extract the latest human message so the prompt can focus on the CURRENT
    # query rather than defaulting to a prior session's research topic.
    latest_user_text = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
            latest_user_text = msg.content
            break

    # Generate research brief from conversation history
    group_start("scope", "Understanding your query", "think")
    emit_think_step("scope", "Reviewing conversation context")

    response = invoke_with_retry(
        structured_output_model,
        [
            HumanMessage(
                content=transform_messages_into_research_topic_prompt.format(
                    messages=get_buffer_string(state.get("messages", [])),
                    date=get_today_str(),
                    current_query=latest_user_text or "(see conversation history)",
                )
            )
        ],
    )

    group_end("scope", "Research brief ready")

    # Update state with generated research brief and pass it to the supervisor
    return {
        "research_brief": response.research_brief,
        "supervisor_messages": [HumanMessage(content=f"{response.research_brief}.")]
    }

# ===== GRAPH CONSTRUCTION =====

# Build the scoping workflow
deep_researcher_builder = StateGraph(AgentState, input_schema=AgentInputState)

# Add workflow nodes
deep_researcher_builder.add_node("load_memory", load_memory)
deep_researcher_builder.add_node("compact_conversation", compact_conversation)
deep_researcher_builder.add_node("clarify_with_user", clarify_with_user)
deep_researcher_builder.add_node("write_research_brief", write_research_brief)

# Add workflow edges
# START -> load_memory (inject memory + persist) -> compact_conversation
# (summarize if the chat is long) -> clarify_with_user (routes onward)
deep_researcher_builder.add_edge(START, "load_memory")
deep_researcher_builder.add_edge("load_memory", "compact_conversation")
deep_researcher_builder.add_edge("compact_conversation", "clarify_with_user")
deep_researcher_builder.add_edge("write_research_brief", END)

# Compile the workflow
scope_research = deep_researcher_builder.compile()
