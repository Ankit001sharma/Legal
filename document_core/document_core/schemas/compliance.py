"""Compliance review output schemas."""

from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ComplianceStatus(str, Enum):
    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"
    INCONCLUSIVE = "INCONCLUSIVE"
    INSUFFICIENT_POLICY_CONTEXT = "INSUFFICIENT_POLICY_CONTEXT"
    POLICY_CONFLICT = "POLICY_CONFLICT"


class Severity(str, Enum):
    CRITICAL = "critical"
    IMPORTANT = "important"
    INFO = "info"


class ComplianceFinding(BaseModel):
    finding_id: str
    dimension_id: str
    dimension_label: str
    status: ComplianceStatus
    severity: Severity = Severity.INFO
    contract_quote: str = ""
    policy_quote: str = ""
    contract_section_id: str | None = None
    policy_section_id: str | None = None
    policy_document_id: UUID | None = None
    rationale: str = ""
    grounded: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewReport(BaseModel):
    tenant_id: str
    contract_document_id: UUID
    contract_title: str
    findings: list[ComplianceFinding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    structure_confidence: str = "high"
    summary_markdown: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
