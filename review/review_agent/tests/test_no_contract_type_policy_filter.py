"""Phase 38.6 — review_agent must not filter policies by applies_to_contract_types."""

from __future__ import annotations

from pathlib import Path

FORBIDDEN = "applies_to_contract_types"
REVIEW_AGENT_ROOT = Path(__file__).resolve().parents[1] / "review_agent"


def test_no_applies_to_contract_types_in_review_agent() -> None:
    offenders: list[str] = []
    for path in REVIEW_AGENT_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if FORBIDDEN in text:
            offenders.append(str(path.relative_to(REVIEW_AGENT_ROOT.parent)))
    assert not offenders, f"forbidden token in: {offenders}"
