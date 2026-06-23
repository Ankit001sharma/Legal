"""Tests to verify LangGraph workflow architectures, node registrations, and transitions."""

from deep_research_from_scratch.multi_agent_supervisor import supervisor_agent
from deep_research_from_scratch.research_agent import researcher_agent
from deep_research_from_scratch.research_agent_full import deep_researcher_builder
from deep_research_from_scratch.research_agent_normal import normal_researcher_builder
from deep_research_from_scratch.research_agent_scope import scope_research

agent = deep_researcher_builder.compile()
normal_agent = normal_researcher_builder.compile()


def test_full_agent_graph_wiring():
    """Verify nodes in the full multi-agent research graph are registered correctly."""
    nodes = agent.get_graph().nodes
    node_names = set(nodes.keys())

    # Assert all core nodes are present
    assert "load_memory" in node_names
    assert "compact_conversation" in node_names
    assert "clarify_with_user" in node_names
    assert "write_research_brief" in node_names
    assert "bootstrap_research" in node_names
    assert "supervisor_subgraph" in node_names
    assert "enrich_sources" in node_names
    assert "validate_and_score_sources" in node_names
    assert "extract_evidence" in node_names
    assert "final_report_generation" in node_names
    assert "verify_report" in node_names
    assert "sanitize_report" in node_names
    assert "finalize_report" in node_names


def test_scope_graph_wiring():
    """Verify nodes in the scoping graph are registered correctly."""
    nodes = scope_research.get_graph().nodes
    node_names = set(nodes.keys())

    assert "load_memory" in node_names
    assert "compact_conversation" in node_names
    assert "clarify_with_user" in node_names
    assert "write_research_brief" in node_names


def test_normal_agent_graph_wiring():
    """Verify the normal research graph includes the clarification gate."""
    nodes = normal_agent.get_graph().nodes
    node_names = set(nodes.keys())

    assert "load_memory" in node_names
    assert "compact_conversation" in node_names
    assert "clarify_with_user" in node_names
    assert "write_research_brief" in node_names
    assert "normal_researcher" in node_names
    assert "generate_normal_answer" in node_names


def test_researcher_agent_graph_wiring():
    """Verify nodes in the standard research agent graph are registered correctly, including compaction."""
    nodes = researcher_agent.get_graph().nodes
    node_names = set(nodes.keys())

    assert "compact_research_context" in node_names
    assert "llm_call" in node_names
    assert "tool_node" in node_names
    assert "compress_research" in node_names


def test_supervisor_agent_graph_wiring():
    """Verify nodes in the multi-agent supervisor graph are registered correctly, including compaction."""
    nodes = supervisor_agent.get_graph().nodes
    node_names = set(nodes.keys())

    assert "compact_supervisor_context" in node_names
    assert "supervisor" in node_names
    assert "supervisor_tools" in node_names
