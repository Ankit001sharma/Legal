"""Tests for discovery graph nodes (scoped policy IDs only)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from review_agent.graph.discovery_nodes import policy_discovery_node
from review_agent.schemas.discovered_policy import DiscoveredPolicy


@pytest.mark.asyncio
async def test_discovery_node_requires_policy_document_ids_in_request_scope(monkeypatch):
    monkeypatch.setenv("REVIEW_POLICY_SCOPE", "request")
    from review_agent.config import get_settings

    get_settings.cache_clear()
    client = AsyncMock()
    with pytest.raises(ValueError, match="policy_document_ids is required"):
        await policy_discovery_node({"tenant_id": "demo", "compliance_stats": {}}, client)


@pytest.mark.asyncio
async def test_discovery_node_indexed_from_topics(monkeypatch):
    monkeypatch.setenv("REVIEW_POLICY_SCOPE", "indexed")
    from review_agent.config import get_settings

    get_settings.cache_clear()
    policy_id = str(uuid4())
    discovered = [
        DiscoveredPolicy(
            document_id=policy_id,
            title="Liability Policy",
            match_score=0.9,
            policy_group="liability",
            categories=["liability"],
        )
    ]
    client = AsyncMock()
    with patch(
        "review_agent.graph.discovery_nodes.discover_policies_from_topics",
        AsyncMock(return_value=(discovered, [], {"discovery_returned": 1})),
    ):
        updates = await policy_discovery_node(
            {
                "tenant_id": "e2e-demo",
                "contract_sections": [],
                "contract_routing": {"topics": ["liability"]},
                "compliance_stats": {},
            },
            client,
        )

    assert updates["policy_document_ids"] == [policy_id]
    assert updates["compliance_stats"]["discovery_scope_mode"] == "indexed"


@pytest.mark.asyncio
async def test_discovery_node_scoped_seed():
    policy_id = str(uuid4())
    seeded = {
        policy_id: DiscoveredPolicy(
            document_id=policy_id,
            title="SLA Policy",
            match_score=1.0,
            policy_group="sla",
            categories=["sla"],
        )
    }
    client = AsyncMock()
    with patch(
        "review_agent.graph.discovery_nodes.seed_discovered_from_scope",
        AsyncMock(return_value=seeded),
    ):
        updates = await policy_discovery_node(
            {
                "tenant_id": "e2e-demo",
                "policy_document_ids": [policy_id],
                "contract_sections": [],
                "compliance_stats": {},
            },
            client,
        )

    assert updates["policy_document_ids"] == [policy_id]
    assert updates["compliance_stats"]["discovery_scope_mode"] == "request"
    assert updates["compliance_stats"]["discovery_returned"] == 1
