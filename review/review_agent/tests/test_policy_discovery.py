"""Tests for tenant policy discovery (Phase 6 Pass 2 + P2-G grouping)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.integration
from httpx import ASGITransport, AsyncClient

from document_core.schemas.chunk import DocumentKind, IngestRequest
from mcp.document_server.main import app
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings
from review_agent.services import policy_discovery
from review_agent.services.policy_discovery import discover_policies_from_topics
from tests.fixtures import SAMPLE_POLICY


@pytest.mark.asyncio
async def test_discover_policies_by_liability_topic():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="Vendor Policy",
                kind=DocumentKind.POLICY,
                text=SAMPLE_POLICY,
                applies_to_contract_types=["msa"],
            )
        )
        settings = ReviewSettings()
        discovered, warnings, _meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=["limitation of liability", "indemnification"],
            contract_type="msa",
            policy_type=None,
            settings=settings,
        )

    assert not warnings
    assert len(discovered) == 1
    assert discovered[0].title
    assert discovered[0].match_score > 0
    assert discovered[0].matched_topics


@pytest.mark.asyncio
async def test_discover_policies_empty_store_warning():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        settings = ReviewSettings()
        discovered, warnings, _meta = await discover_policies_from_topics(
            client,
            tenant_id="empty",
            topics=["limitation of liability"],
            contract_type=None,
            policy_type=None,
            settings=settings,
        )

    assert discovered == []
    assert len(warnings) == 1
    assert "No policies discovered" in warnings[0]


@pytest.mark.asyncio
async def test_discover_policies_respects_max_cap():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        for idx in range(3):
            await client.index_policy(
                IngestRequest(
                    tenant_id="demo",
                    title=f"Policy {idx}",
                    kind=DocumentKind.POLICY,
                    text=f"{idx}. Limitation of Liability\nCap applies.\n",
                )
            )
        settings = ReviewSettings(
            discovery_group_mode="flat",
            discovery_max_policies=1,
        )
        discovered, _, _meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=["limitation of liability"],
            contract_type=None,
            policy_type=None,
            settings=settings,
        )

    assert len(discovered) == 1


@pytest.mark.asyncio
async def test_discover_policies_cap_emits_warning():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        for idx in range(3):
            await client.index_policy(
                IngestRequest(
                    tenant_id="demo",
                    title=f"Policy {idx}",
                    kind=DocumentKind.POLICY,
                    text=f"{idx}. Limitation of Liability\nCap applies.\n",
                )
            )
        settings = ReviewSettings(
            discovery_group_mode="flat",
            discovery_max_policies=1,
            discovery_warn_on_cap=True,
        )
        discovered, warnings, meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=["limitation of liability"],
            contract_type=None,
            policy_type=None,
            settings=settings,
        )

    assert len(discovered) == 1
    assert meta["discovery_capped"] is True
    assert any("capped at 1" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_discovery_groups_by_category():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        for idx, score_text in enumerate(
            (
                "Limitation of Liability cap one hundred thousand dollars.",
                "Limitation of Liability cap twelve months fees.",
                "Human Rights forced labor due diligence OECD.",
            )
        ):
            category = "liability" if idx < 2 else "human_rights"
            await client.index_policy(
                IngestRequest(
                    tenant_id="demo",
                    title=f"Policy {idx}",
                    kind=DocumentKind.POLICY,
                    text=f"{idx}. {score_text}",
                    categories=[category],
                )
            )
        settings = ReviewSettings(discovery_max_policies=0)
        discovered, warnings, meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=["limitation of liability", "human rights forced labor"],
            contract_type=None,
            policy_type=None,
            settings=settings,
        )

    assert len(discovered) == 2
    groups = {policy.policy_group for policy in discovered}
    assert groups == {"liability", "human_rights"}
    assert meta["discovery_deduped"] >= 1
    assert any("duplicate-category" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_discovery_group_cap_six():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        category_topics = [
            ("compliance", "supplier code of conduct compliance"),
            ("human_rights", "human rights forced labor due diligence"),
            ("minerals", "responsible minerals sourcing tin tungsten"),
            ("environment", "environment greenhouse gas emissions"),
            ("security", "information security MSS requirements"),
            ("vendor_security", "vendor security assessment controls"),
            ("privacy", "data privacy personal information"),
            ("termination", "termination notice period breach"),
        ]
        for idx, (category, _topic) in enumerate(category_topics):
            await client.index_policy(
                IngestRequest(
                    tenant_id="demo",
                    title=f"Policy {category}",
                    kind=DocumentKind.POLICY,
                    text=f"{idx}. {category} policy requirements and obligations.",
                    categories=[category],
                )
            )
        settings = ReviewSettings(
            discovery_group_cap_mode="fixed",
            discovery_max_policy_groups=6,
            discovery_max_policies=0,
            discovery_max_topics=0,
            discovery_topic_cap_mode="fixed",
            discovery_warn_on_cap=True,
        )
        discovered, warnings, meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=[topic for _category, topic in category_topics],
            contract_type=None,
            policy_type=None,
            settings=settings,
        )

    assert len(discovered) == 6
    assert meta["discovery_groups"] == 6
    assert meta["discovery_total_ranked"] >= 8
    assert any("group cap at 6" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_discovery_flat_mode_legacy():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        for idx in range(3):
            await client.index_policy(
                IngestRequest(
                    tenant_id="demo",
                    title=f"Liability Policy {idx}",
                    kind=DocumentKind.POLICY,
                    text=f"{idx}. Limitation of Liability cap applies.",
                    categories=["liability"],
                )
            )
        settings = ReviewSettings(
            discovery_group_mode="flat",
            discovery_max_policies=2,
            discovery_warn_on_cap=True,
        )
        discovered, warnings, meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=["limitation of liability"],
            contract_type=None,
            policy_type=None,
            settings=settings,
        )

    assert len(discovered) == 2
    assert meta["discovery_group_mode"] == "flat"
    assert meta["discovery_deduped"] == 0
    assert any("capped at 2" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_discovery_topics_capped():
    client = AsyncMock()
    client.search_policy = AsyncMock(return_value=[])
    settings = ReviewSettings(discovery_max_topics=2, discovery_topic_cap_mode="fixed")
    await discover_policies_from_topics(
        client,
        tenant_id="demo",
        topics=["alpha", "beta", "gamma", "delta"],
        contract_type=None,
        policy_type=None,
        settings=settings,
    )
    assert client.search_policy.await_count == 2


def test_policy_group_key_prefers_category():
    key = policy_discovery._policy_group_key(
        categories=["human_rights", "labor"],
        metadata={},
        matched_topics=["forced labor"],
        document_id="abc",
    )
    assert key == "human_rights"


def test_select_grouped_policies_keeps_best_score_per_group():
    from review_agent.schemas.discovered_policy import DiscoveredPolicy

    ranked = [
        DiscoveredPolicy(document_id="1", match_score=0.9, policy_group="liability"),
        DiscoveredPolicy(document_id="2", match_score=0.5, policy_group="liability"),
        DiscoveredPolicy(document_id="3", match_score=0.8, policy_group="human_rights"),
    ]
    grouped, deduped, groups_before = policy_discovery._select_grouped_policies(
        ranked,
        max_groups=6,
        max_policies=0,
    )
    assert deduped == 1
    assert groups_before == 2
    assert len(grouped) == 2
    assert grouped[0].document_id == "1"
    assert grouped[1].document_id == "3"


def test_resolve_discovery_group_cap_adaptive():
    settings = ReviewSettings(
        discovery_group_cap_mode="adaptive",
        discovery_min_policy_groups=6,
        discovery_max_policy_groups_ceiling=20,
    )
    cap = policy_discovery.resolve_discovery_group_cap(
        settings=settings,
        reviewable_section_count=20,
        unique_category_count=15,
    )
    assert cap == 15


def test_resolve_discovery_group_cap_cisco_floor():
    settings = ReviewSettings(discovery_group_cap_mode="adaptive")
    cap = policy_discovery.resolve_discovery_group_cap(
        settings=settings,
        reviewable_section_count=6,
        unique_category_count=5,
    )
    assert cap == 6


def test_resolve_topic_cap_adaptive():
    settings = ReviewSettings(
        discovery_topic_cap_mode="adaptive",
        discovery_max_topics_ceiling=20,
    )
    assert policy_discovery.resolve_topic_cap(settings=settings, topic_count=18) == 18


@pytest.mark.asyncio
async def test_contract_type_fallback_niche():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="MSA Liability",
                kind=DocumentKind.POLICY,
                text="1. Limitation of Liability\nFees paid in twelve months.",
                categories=["liability"],
                applies_to_contract_types=["msa"],
            )
        )
        await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="MSA Indemnity",
                kind=DocumentKind.POLICY,
                text="2. Indemnification\nVendor shall indemnify.",
                categories=["indemnity"],
                applies_to_contract_types=["msa"],
            )
        )
        settings = ReviewSettings(
            discovery_group_cap_mode="fixed",
            discovery_max_policy_groups=0,
            discovery_max_policies=0,
            discovery_contract_type_fallback_min_hits=2,
            discovery_section_category_sweep=False,
        )
        discovered, warnings, meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=["limitation of liability", "indemnification"],
            contract_type="oem",
            policy_type=None,
            settings=settings,
        )

    assert len(discovered) >= 2
    assert meta["discovery_contract_type_relaxed"] is True
    assert any("relaxed contract_type" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_category_sweep_adds_minerals():
    from uuid import UUID

    from document_core.schemas.chunk import ChunkRole, IndexedChunk

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        await client.index_policy(
            IngestRequest(
                tenant_id="demo",
                title="Minerals Policy",
                kind=DocumentKind.POLICY,
                text="1. Responsible Minerals\nSubmit MRT templates.",
                categories=["minerals"],
            )
        )
        settings = ReviewSettings(
            discovery_group_cap_mode="fixed",
            discovery_max_policy_groups=0,
            discovery_max_policies=0,
            discovery_section_category_sweep=True,
        )
        section = IndexedChunk(
            chunk_id="c-3",
            document_id=UUID("00000000-0000-0000-0000-000000000001"),
            tenant_id="demo",
            kind=DocumentKind.CONTRACT,
            chunk_role=ChunkRole.PARENT,
            section_id="3",
            section_path="3",
            title="Responsible Minerals",
            text="Supplier is not obligated to complete Minerals Reporting Templates.",
        )
        discovered, _warnings, meta = await discover_policies_from_topics(
            client,
            tenant_id="demo",
            topics=[],
            contract_type=None,
            policy_type=None,
            settings=settings,
            contract_sections=[section],
        )

    assert len(discovered) == 1
    assert discovered[0].policy_group == "minerals"
    assert "minerals" in meta["discovery_section_categories"]
    assert meta["discovery_category_sweep_added"] >= 1
