"""~10-page enterprise contract + 85 real-world-style playbooks for mega stress test."""

from __future__ import annotations

from typing import Any

from beta_test.scale_corpus import POLICY_LIBRARY, SECTION_TEMPLATES, _policy_fixture

TENANT = "enterprise-mega"
TARGET_POLICY_COUNT = 85
TARGET_SECTION_COUNT = 40  # ~10 pages at ~200 words/section

_REGION_VARIANTS: list[tuple[str, str]] = [
    ("", ""),
    ("-global", " — Global Standard"),
    ("-eu", " — EU / GDPR"),
    ("-us", " — US Federal"),
    ("-apac", " — APAC"),
]


def expand_policy_library(*, target: int = TARGET_POLICY_COUNT) -> list[dict[str, Any]]:
    """Duplicate base library with regional variants until target count (unique policy_ref)."""
    expanded: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    round_idx = 0
    while len(expanded) < target:
        for base in POLICY_LIBRARY:
            suffix, title_suffix = _REGION_VARIANTS[round_idx % len(_REGION_VARIANTS)]
            policy_ref = f"{base['policy_ref']}{suffix}"
            if suffix and round_idx >= len(_REGION_VARIANTS):
                policy_ref = f"{base['policy_ref']}-v{round_idx}{suffix}"
            if policy_ref in seen_refs:
                continue
            seen_refs.add(policy_ref)
            item = dict(base)
            item["policy_ref"] = policy_ref
            item["title"] = f"{base['title']}{title_suffix}"
            if round_idx > 0:
                item["text"] = (
                    f"{base['text']} Regional addendum {round_idx}: "
                    "controls apply to in-scope subsidiaries and approved sub-processors."
                )
            expanded.append(item)
            if len(expanded) >= target:
                break
        round_idx += 1
    return expanded


def _substantive_padding(category: str, *, pass_idx: int) -> str:
    """Category-specific clause depth (~150 words) — unique per section, not shared boilerplate."""
    blocks = {
        "sla": (
            " Service credits shall be calculated monthly based on measured uptime excluding "
            "scheduled maintenance windows approved in writing. Vendor maintains redundant "
            "capacity in at least two availability zones. Root-cause analysis for P1 incidents "
            "due within five business days."
        ),
        "ai_usage": (
            " Model training, fine-tuning, and evaluation datasets must be documented. "
            "Customer may audit data lineage annually. High-risk automated decisions require "
            "human-in-the-loop review and appeal mechanism."
        ),
        "liability": (
            " Carve-outs from the liability cap include breaches of confidentiality, "
            "indemnification obligations, and violations of applicable data protection law. "
            "Neither party excludes liability for fraud or willful misconduct."
        ),
        "security": (
            " Vendor maintains SOC 2 Type II or equivalent. Encryption in transit uses TLS 1.2+ "
            "and at rest uses AES-256 or better. Incident response runbooks tested semi-annually."
        ),
        "compliance": (
            " Supplier participates in Buyer audit programs upon reasonable notice. "
            "Corrective action plans for material findings due within thirty days."
        ),
    }
    base = blocks.get(
        category,
        (
            " Performance under this section is measured against industry standards for "
            f"{'primary' if pass_idx == 0 else 'annex'} obligations. Subcontractor flow-down "
            "required where subprocessors perform in-scope services."
        ),
    )
    return base


def build_mega_contract(*, tenant_id: str = TENANT) -> dict[str, Any]:
    """40-section MSA (~10 pages) — substantive clauses only, no shared boilerplate tail."""
    sections: list[dict[str, Any]] = []
    eval_labels: dict[str, dict[str, Any]] = {}
    templates = list(SECTION_TEMPLATES)
    # Part I: 20 sections (weak bias); Part II: 20 annex sections (mixed)
    for pass_idx in range(2):
        prefix = "" if pass_idx == 0 else "Annex — "
        for idx, (title, category, weak, strong) in enumerate(templates, start=1):
            section_num = pass_idx * len(templates) + idx
            if section_num > TARGET_SECTION_COUNT:
                break
            expect_gap = (section_num + pass_idx) % 3 != 0
            body = weak if expect_gap else strong
            body = body + _substantive_padding(category, pass_idx=pass_idx)
            full_title = f"{prefix}{title}" if prefix else title
            sections.append(
                {
                    "section_id": str(section_num),
                    "title": full_title,
                    "text": body,
                }
            )
            eval_labels[str(section_num)] = {
                "category": category,
                "expect_gap": expect_gap,
                "title": full_title,
            }

    word_estimate = sum(len(s["text"].split()) for s in sections)
    return {
        "tenant_id": tenant_id,
        "contract_ref": "enterprise-mega-msa-2026",
        "title": "Global Master Services Agreement — Fortune 500 Buyer / Multi-Region Vendor Consortium",
        "contract_type": "msa",
        "metadata": {
            "source": "enterprise-mega-benchmark",
            "page_estimate": "9-11",
            "section_count": len(sections),
            "word_estimate": word_estimate,
            "eval_labels": eval_labels,
            "policy_target": TARGET_POLICY_COUNT,
        },
        "sections": sections,
    }


def build_mega_corpus(*, tenant_id: str = TENANT) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    policies_raw = expand_policy_library()
    policies = [_policy_fixture(item, tenant_id=tenant_id) for item in policies_raw]
    contract = build_mega_contract(tenant_id=tenant_id)
    return contract, policies


def policy_count() -> int:
    return len(expand_policy_library())


def section_count() -> int:
    return TARGET_SECTION_COUNT
