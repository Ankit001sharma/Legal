"""Review agent runtime configuration — section-first production pipeline."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class ReviewSettings(BaseSettings):
    """Settings for section-first compliance review."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    compliance_llm_temperature: float = 0.0
    compliance_llm_max_retries: int = 1
    compliance_llm_role: str = "reasoning"
    compliance_llm_max_tokens: int = 2048

    review_min_section_chars: int = 40

    policy_catalog_url: str | None = None
    policy_fetch_enabled: bool = True

    review_policy_scope: Literal["request", "tenant", "discovered"] = "discovered"
    contract_routing_mode: Literal["llm", "lexical"] = "llm"
    contract_routing_max_chars: int = 12_000
    discovery_max_policies: int = 50
    discovery_top_k_per_topic: int = 5
    discovery_min_score: float = 0.08

    section_classify_batch_size: int = 2
    section_classify_max_chars: int = 12_000
    retrieval_recall_top_k: int = 20
    retrieval_final_top_k: int = 10
    retrieval_max_attempts: int = 3
    retrieval_broaden_on_retry: bool = True
    retrieval_category_hard_filter: bool = True
    retrieval_category_filter_fallback: bool = True
    discovery_warn_on_cap: bool = True
    section_compare_batch_size: int = 2
    section_compare_max_tokens: int = 48_000
    section_compare_max_section_chars: int = 32_000
    section_retrieval_concurrency: int = 8
    section_compare_concurrency: int = 3

    final_gap_verify_enabled: bool = True
    final_gap_recall_top_k: int = 30

    enforce_section_coverage: bool = True
    review_require_contract_document_id: bool = False
    review_reject_inline_policies: bool = False
    review_preflight_enabled: bool = True

    playbook_enrich_compare: bool = True
    playbook_load_registry: bool = False
    grounding_downgrade_not_drop: bool = True
    grounding_rerun_coverage: bool = True
    conflict_emit_on_skip: bool = False

    artifact_include_hit_refs: bool = True
    artifact_max_hit_refs_per_section: int = 10
    report_llm_summary: bool = False
    report_llm_summary_max_tokens: int = 256

    guard_pass_enabled: bool = True
    guard_pass_mode: Literal["llm"] = "llm"
    guard_pass_concurrency: int = 4


@lru_cache
def get_settings() -> ReviewSettings:
    return ReviewSettings()
