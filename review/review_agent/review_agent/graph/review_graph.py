"""Compiled LangGraph review pipeline."""

from __future__ import annotations

import uuid
from functools import partial

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.clients.memory_client import MemoryMCPClient
from review_agent.graph.memory_nodes import load_memory_node, save_review_memory_node
from review_agent.graph.nodes import (
    clause_detection_node,
    compliance_review_node,
    contract_parser_node,
    grounding_node,
    index_policies_node,
    policy_retrieval_node,
    report_node,
)
from review_agent.state.review_state import ReviewState

_checkpointer = MemorySaver()


def build_review_graph(
    client: DocumentMCPClient,
    memory_client: MemoryMCPClient | None = None,
):
    """Build and compile the text-only compliance review graph."""
    graph = StateGraph(ReviewState)

    graph.add_node("load_memory", partial(load_memory_node, memory_client=memory_client))
    graph.add_node("index_policies", partial(index_policies_node, client=client))
    graph.add_node("contract_parser", partial(contract_parser_node, client=client))
    graph.add_node("clause_detection", partial(clause_detection_node, client=client))
    graph.add_node("policy_retrieval", partial(policy_retrieval_node, client=client))
    graph.add_node("compliance_review", partial(compliance_review_node, client=client))
    graph.add_node("grounding", partial(grounding_node, client=client))
    graph.add_node("report", partial(report_node, client=client))
    graph.add_node(
        "save_memory",
        partial(save_review_memory_node, memory_client=memory_client),
    )

    graph.add_edge(START, "load_memory")
    graph.add_edge("load_memory", "index_policies")
    graph.add_edge("index_policies", "contract_parser")
    graph.add_edge("contract_parser", "clause_detection")
    graph.add_edge("clause_detection", "policy_retrieval")
    graph.add_edge("policy_retrieval", "compliance_review")
    graph.add_edge("compliance_review", "grounding")
    graph.add_edge("grounding", "report")
    graph.add_edge("report", "save_memory")
    graph.add_edge("save_memory", END)

    return graph.compile(checkpointer=_checkpointer)


async def run_review(
    *,
    client: DocumentMCPClient,
    tenant_id: str,
    contract_text: str,
    contract_title: str = "Contract",
    policy_texts: list[dict] | None = None,
    contract_type: str | None = None,
    policy_type: str | None = None,
    memory_client: MemoryMCPClient | None = None,
    memory_context: str = "",
    thread_id: str | None = None,
) -> ReviewState:
    """Run review graph with optional retrieval MCP memory + session checkpoint."""
    session_id = thread_id or str(uuid.uuid4())
    graph = build_review_graph(client, memory_client=memory_client)
    initial: ReviewState = {
        "tenant_id": tenant_id,
        "contract_text": contract_text,
        "contract_title": contract_title,
        "policy_texts": policy_texts or [],
        "contract_type": contract_type,
        "policy_type": policy_type,
        "thread_id": session_id,
        "findings": [],
        "warnings": [],
        "memory_context": memory_context,
        "memory_hits": [],
    }
    config = {"configurable": {"thread_id": session_id}}
    return await graph.ainvoke(initial, config=config)
