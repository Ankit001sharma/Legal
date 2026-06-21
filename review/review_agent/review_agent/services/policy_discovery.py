"""Discover tenant policy documents by routing topics (Pass 2 — no LLM)."""

from __future__ import annotations

from typing import Any

from document_core.schemas.chunk import DocumentKind, IndexedChunk, SearchRequest
from document_core.schemas.taxonomy import normalize_categories

from review_agent.clients.document_client import DocumentMCPClient
from review_agent.config import ReviewSettings
from review_agent.schemas.discovered_policy import DiscoveredPolicy
from review_agent.services.section_category_lexical import (
    _CATEGORY_QUERY_TERMS,
    infer_lexical_classify,
)


def _cap_topics(topics: list[str], *, max_topics: int) -> list[str]:
    if max_topics <= 0:
        return [topic.strip() for topic in topics if topic.strip()]
    out: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        cleaned = topic.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if len(out) >= max_topics:
            break
    return out


def resolve_topic_cap(*, settings: ReviewSettings, topic_count: int) -> int:
    """How many routing topics to search (fixed vs adaptive ceiling)."""
    if topic_count <= 0:
        return 0
    if settings.discovery_topic_cap_mode == "fixed":
        max_topics = settings.discovery_max_topics
        return topic_count if max_topics <= 0 else min(topic_count, max_topics)
    ceiling = settings.discovery_max_topics_ceiling
    if ceiling <= 0:
        return topic_count
    return min(topic_count, ceiling)


def resolve_discovery_group_cap(
    *,
    settings: ReviewSettings,
    reviewable_section_count: int,
    unique_category_count: int,
) -> int:
    """Adaptive group cap: Cisco-sized contracts stay at 6; enterprise scales up."""
    if settings.discovery_group_cap_mode == "fixed":
        return settings.discovery_max_policy_groups
    if settings.discovery_max_policy_groups <= 0:
        return 0
    target = max(
        settings.discovery_min_policy_groups,
        unique_category_count,
        (reviewable_section_count + 1) // 2,
    )
    ceiling = settings.discovery_max_policy_groups_ceiling
    if ceiling <= 0:
        return target
    return min(target, ceiling)


def unique_categories_from_sections(sections: list[IndexedChunk]) -> list[str]:
    """Lexical taxonomy categories inferred from contract sections (0 LLM)."""
    seen: set[str] = set()
    ordered: list[str] = []
    for section in sections:
        lex = infer_lexical_classify(section)
        for category in lex.categories:
            if category == "general" or category in seen:
                continue
            seen.add(category)
            ordered.append(category)
    return ordered


def _categories_from_parent(parent: IndexedChunk) -> list[str]:
    raw = parent.metadata.get("categories")
    if not isinstance(raw, list):
        return []
    return normalize_categories(raw)


def _policy_group_key(
    *,
    categories: list[str],
    metadata: dict[str, Any],
    matched_topics: list[str],
    document_id: str,
) -> str:
    explicit = (
        (metadata.get("policy_group") or metadata.get("playbook_group") or "").strip()
    )
    if explicit:
        return explicit.lower()

    if categories:
        return categories[0]

    if matched_topics:
        return matched_topics[0].lower().replace(" ", "_")[:64]

    return f"doc:{document_id}"


def _merge_categories(existing: list[str], new: list[str]) -> list[str]:
    return normalize_categories([*existing, *new])


def _build_discovered_policy(
    *,
    parent: IndexedChunk,
    doc_id: str,
    doc_title: str,
    topic_clean: str,
    score: float,
    applies: list[str],
    existing: DiscoveredPolicy | None,
) -> DiscoveredPolicy:
    parent_categories = _categories_from_parent(parent)
    if existing is None:
        matched_topics = [topic_clean]
        categories = list(parent_categories)
        match_score = score
        title = doc_title or parent.title or ""
        policy_type = parent.policy_type
        applies_to = list(applies)
    else:
        matched_topics = list(existing.matched_topics)
        if topic_clean not in matched_topics:
            matched_topics.append(topic_clean)
        categories = _merge_categories(existing.categories, parent_categories)
        match_score = max(existing.match_score, score)
        title = existing.title or doc_title or parent.title or ""
        policy_type = existing.policy_type or parent.policy_type
        applies_to = existing.applies_to_contract_types or list(applies)

    policy_group = _policy_group_key(
        categories=categories,
        metadata=parent.metadata or {},
        matched_topics=matched_topics,
        document_id=doc_id,
    )
    return DiscoveredPolicy(
        document_id=doc_id,
        title=title,
        policy_type=policy_type,
        match_score=match_score,
        matched_topics=matched_topics,
        applies_to_contract_types=applies_to,
        policy_group=policy_group,
        categories=categories,
    )


def _merge_aggregated(
    base: dict[str, DiscoveredPolicy],
    extra: dict[str, DiscoveredPolicy],
) -> dict[str, DiscoveredPolicy]:
    for doc_id, policy in extra.items():
        if doc_id not in base:
            base[doc_id] = policy
            continue
        existing = base[doc_id]
        merged_topics = list(existing.matched_topics)
        for topic in policy.matched_topics:
            if topic not in merged_topics:
                merged_topics.append(topic)
        base[doc_id] = DiscoveredPolicy(
            document_id=existing.document_id,
            title=existing.title or policy.title,
            policy_type=existing.policy_type or policy.policy_type,
            match_score=max(existing.match_score, policy.match_score),
            matched_topics=merged_topics,
            applies_to_contract_types=existing.applies_to_contract_types or policy.applies_to_contract_types,
            policy_group=existing.policy_group or policy.policy_group,
            categories=_merge_categories(existing.categories, policy.categories),
        )
    return base


def _select_grouped_policies(
    ranked: list[DiscoveredPolicy],
    *,
    max_groups: int,
    max_policies: int,
) -> tuple[list[DiscoveredPolicy], int, int]:
    """One best policy per group key; then cap groups and optional flat cap."""
    best_by_group: dict[str, DiscoveredPolicy] = {}
    for policy in ranked:
        key = policy.policy_group or policy.document_id
        if key not in best_by_group:
            best_by_group[key] = policy

    grouped = sorted(best_by_group.values(), key=lambda item: item.match_score, reverse=True)
    deduped_count = len(ranked) - len(grouped)
    groups_before_cap = len(grouped)

    if max_groups > 0:
        grouped = grouped[:max_groups]
    if max_policies > 0:
        grouped = grouped[:max_policies]
    return grouped, deduped_count, groups_before_cap


def _apply_flat_cap(
    ranked: list[DiscoveredPolicy],
    *,
    max_policies: int,
) -> list[DiscoveredPolicy]:
    if max_policies <= 0:
        return ranked
    return ranked[:max_policies]


def _query_for_category(category: str) -> str:
    terms = _CATEGORY_QUERY_TERMS.get(category)
    if terms:
        return terms[0]
    return category.replace("_", " ")


async def _search_topics(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    topics: list[str],
    contract_type: str | None,
    policy_type: str | None,
    settings: ReviewSettings,
) -> dict[str, DiscoveredPolicy]:
    aggregated: dict[str, DiscoveredPolicy] = {}
    for topic_clean in topics:
        hits = await client.search_policy(
            SearchRequest(
                tenant_id=tenant_id,
                query=topic_clean,
                kind=DocumentKind.POLICY,
                contract_type=contract_type,
                policy_type=policy_type,
                top_k=settings.discovery_top_k_per_topic,
            )
        )
        for hit in hits:
            if hit.score < settings.discovery_min_score:
                continue
            parent = hit.parent_chunk
            doc_id = str(parent.document_id)
            doc_title = str(parent.metadata.get("document_title") or "").strip() or parent.title or ""
            applies = list(parent.applies_to_contract_types or [])
            aggregated[doc_id] = _build_discovered_policy(
                parent=parent,
                doc_id=doc_id,
                doc_title=doc_title,
                topic_clean=topic_clean,
                score=hit.score,
                applies=applies,
                existing=aggregated.get(doc_id),
            )
    return aggregated


async def _discover_by_section_categories(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    categories: list[str],
    contract_type: str | None,
    policy_type: str | None,
    settings: ReviewSettings,
) -> dict[str, DiscoveredPolicy]:
    """Category metadata sweep from lexical section scan (0 LLM)."""
    aggregated: dict[str, DiscoveredPolicy] = {}
    for category in categories:
        if category == "general":
            continue
        query = _query_for_category(category)
        use_contract_type = contract_type if settings.discovery_contract_type_filter else None
        hits = await client.search_policy_by_categories(
            SearchRequest(
                tenant_id=tenant_id,
                query=query,
                kind=DocumentKind.POLICY,
                contract_type=use_contract_type,
                policy_type=policy_type,
                top_k=settings.discovery_top_k_per_topic,
            ),
            categories=[category],
        )
        if (
            settings.discovery_contract_type_filter
            and contract_type
            and not hits
        ):
            hits = await client.search_policy_by_categories(
                SearchRequest(
                    tenant_id=tenant_id,
                    query=query,
                    kind=DocumentKind.POLICY,
                    contract_type=None,
                    policy_type=policy_type,
                    top_k=settings.discovery_top_k_per_topic,
                ),
                categories=[category],
            )
        for hit in hits:
            if hit.score < settings.discovery_min_score:
                continue
            parent = hit.parent_chunk
            doc_id = str(parent.document_id)
            doc_title = str(parent.metadata.get("document_title") or "").strip() or parent.title or ""
            applies = list(parent.applies_to_contract_types or [])
            topic_label = f"section_category:{category}"
            aggregated[doc_id] = _build_discovered_policy(
                parent=parent,
                doc_id=doc_id,
                doc_title=doc_title,
                topic_clean=topic_label,
                score=hit.score,
                applies=applies,
                existing=aggregated.get(doc_id),
            )
    return aggregated


def _group_and_cap(
    ranked: list[DiscoveredPolicy],
    *,
    settings: ReviewSettings,
    group_cap: int,
) -> tuple[list[DiscoveredPolicy], int, int]:
    if settings.discovery_group_mode == "category":
        return _select_grouped_policies(
            ranked,
            max_groups=group_cap,
            max_policies=settings.discovery_max_policies,
        )
    return _apply_flat_cap(ranked, max_policies=settings.discovery_max_policies), 0, len(ranked)


async def discover_policies_from_topics(
    client: DocumentMCPClient,
    *,
    tenant_id: str,
    topics: list[str],
    contract_type: str | None,
    policy_type: str | None,
    settings: ReviewSettings,
    contract_sections: list[IndexedChunk] | None = None,
    reviewable_section_count: int = 0,
) -> tuple[list[DiscoveredPolicy], list[str], dict[str, Any]]:
    """Search tenant policy index per topic; group by category and cap scope."""
    warnings: list[str] = []
    empty_meta = {
        "discovery_total_ranked": 0,
        "discovery_returned": 0,
        "discovery_capped": False,
        "discovery_groups": 0,
        "discovery_deduped": 0,
        "discovery_group_mode": settings.discovery_group_mode,
        "discovery_group_cap_mode": settings.discovery_group_cap_mode,
        "discovery_group_cap_resolved": 0,
        "discovery_max_policies_effective": settings.discovery_max_policies,
        "discovery_contract_type_relaxed": False,
        "discovery_category_sweep_added": 0,
        "discovery_topics_searched": 0,
    }
    if not topics and not contract_sections:
        warnings.append("No routing topics provided; policy discovery skipped.")
        return [], warnings, empty_meta

    section_categories = unique_categories_from_sections(contract_sections or [])
    if reviewable_section_count <= 0 and contract_sections:
        reviewable_section_count = len(contract_sections)

    topic_cap = resolve_topic_cap(
        settings=settings,
        topic_count=len([t for t in topics if t.strip()]),
    )
    search_topics = _cap_topics(topics, max_topics=topic_cap)
    group_cap = resolve_discovery_group_cap(
        settings=settings,
        reviewable_section_count=reviewable_section_count,
        unique_category_count=len(section_categories),
    )

    contract_type_relaxed = False
    use_contract_type = contract_type if settings.discovery_contract_type_filter else None
    strict_aggregated = await _search_topics(
        client,
        tenant_id=tenant_id,
        topics=search_topics,
        contract_type=use_contract_type,
        policy_type=policy_type,
        settings=settings,
    )
    aggregated = dict(strict_aggregated)

    sweep_added = 0
    if settings.discovery_section_category_sweep and section_categories:
        sweep = await _discover_by_section_categories(
            client,
            tenant_id=tenant_id,
            categories=section_categories,
            contract_type=contract_type,
            policy_type=policy_type,
            settings=settings,
        )
        before_ids = set(aggregated.keys())
        aggregated = _merge_aggregated(aggregated, sweep)
        sweep_added = len(set(aggregated.keys()) - before_ids)

    ranked = sorted(aggregated.values(), key=lambda policy: policy.match_score, reverse=True)
    capped, deduped, groups_before_cap = _group_and_cap(
        ranked,
        settings=settings,
        group_cap=group_cap,
    )

    if (
        settings.discovery_contract_type_filter
        and contract_type
        and len(strict_aggregated) == 0
    ):
        fallback_agg = await _search_topics(
            client,
            tenant_id=tenant_id,
            topics=search_topics,
            contract_type=None,
            policy_type=policy_type,
            settings=settings,
        )
        if settings.discovery_section_category_sweep and section_categories:
            sweep = await _discover_by_section_categories(
                client,
                tenant_id=tenant_id,
                categories=section_categories,
                contract_type=contract_type,
                policy_type=policy_type,
                settings=settings,
            )
            fallback_agg = _merge_aggregated(fallback_agg, sweep)
        merged = _merge_aggregated(dict(aggregated), fallback_agg)
        ranked = sorted(merged.values(), key=lambda policy: policy.match_score, reverse=True)
        capped, deduped, groups_before_cap = _group_and_cap(
            ranked,
            settings=settings,
            group_cap=group_cap,
        )
        contract_type_relaxed = True
        if settings.discovery_warn_on_cap:
            warnings.append(
                "Policy discovery relaxed contract_type filter (sparse hits with strict filter)."
            )

    if settings.discovery_group_mode == "category":
        if settings.discovery_warn_on_cap and deduped > 0:
            warnings.append(
                f"Policy discovery grouped {len(ranked)} candidates into "
                f"{len(capped)} playbook families ({deduped} duplicate-category doc(s) omitted)."
            )
        if (
            settings.discovery_warn_on_cap
            and group_cap > 0
            and groups_before_cap > len(capped)
        ):
            warnings.append(
                f"Policy discovery group cap at {group_cap}; "
                f"{groups_before_cap - len(capped)} group(s) omitted."
            )
    elif (
        settings.discovery_warn_on_cap
        and settings.discovery_max_policies > 0
        and len(ranked) > len(capped)
    ):
        warnings.append(
            f"Policy discovery capped at {settings.discovery_max_policies}; "
            f"{len(ranked) - len(capped)} policy(s) omitted "
            "(raise DISCOVERY_MAX_POLICIES or set 0 for unlimited)."
        )

    if (
        settings.discovery_warn_on_cap
        and settings.discovery_group_mode == "category"
        and settings.discovery_max_policies > 0
        and len(ranked) > len(capped)
        and not any("capped at" in warning for warning in warnings)
    ):
        warnings.append(
            f"Policy discovery capped at {settings.discovery_max_policies}; "
            f"{len(ranked) - len(capped)} policy(s) omitted after grouping."
        )

    discovery_meta = {
        "discovery_total_ranked": len(ranked),
        "discovery_returned": len(capped),
        "discovery_capped": len(ranked) > len(capped),
        "discovery_groups": len(capped),
        "discovery_deduped": deduped,
        "discovery_group_mode": settings.discovery_group_mode,
        "discovery_group_cap_mode": settings.discovery_group_cap_mode,
        "discovery_group_cap_resolved": group_cap,
        "discovery_max_policies_effective": settings.discovery_max_policies,
        "discovery_contract_type_relaxed": contract_type_relaxed,
        "discovery_category_sweep_added": sweep_added,
        "discovery_topics_searched": len(search_topics),
        "discovery_section_categories": section_categories,
    }

    if not capped:
        warnings.append(
            f"No policies discovered for tenant '{tenant_id}' from {len(search_topics)} topic(s). "
            "Ensure playbooks are indexed in the document store."
        )

    return capped, warnings, discovery_meta


def discovered_to_indexed_entries(policies: list[DiscoveredPolicy]) -> list[dict]:
    """Map discovery results to indexed_policies metadata shape."""
    return [
        {
            "document_id": p.document_id,
            "title": p.title,
            "policy_type": p.policy_type,
            "applies_to_contract_types": list(p.applies_to_contract_types),
            "discovery_score": p.match_score,
            "matched_topics": list(p.matched_topics),
            "policy_group": p.policy_group,
            "categories": list(p.categories),
        }
        for p in policies
    ]


def parse_discovered_document_ids(policies: list[DiscoveredPolicy]) -> list[str]:
    """Stable document_id list for policy_plan."""
    seen: set[str] = set()
    ordered: list[str] = []
    for policy in policies:
        if policy.document_id not in seen:
            seen.add(policy.document_id)
            ordered.append(policy.document_id)
    return ordered
