"""ReviewState must carry contract_text through LangGraph."""

from review_agent.state.review_state import ReviewState


def test_review_state_includes_contract_text() -> None:
  # TypedDict keys are what LangGraph keeps in channel state.
    state: ReviewState = {
        "tenant_id": "demo",
        "contract_text": "Section 1. Liability cap is $100k.",
    }
    assert state["contract_text"].startswith("Section")
