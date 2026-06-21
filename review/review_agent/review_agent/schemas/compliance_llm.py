"""Structured LLM output for compliance review."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from document_core.schemas.compliance import ComplianceStatus, Severity
from review_agent.schemas.compliance_status_utils import normalize_compliance_status
from review_agent.schemas.quote_field_utils import coerce_quote_field


class ComplianceLLMResult(BaseModel):
    """JSON schema returned by the compliance LLM (mapped to ComplianceFinding)."""

    status: ComplianceStatus
    severity: Severity = Severity.INFO
    contract_quote: str = ""
    policy_quote: str = ""
    rationale: str = Field(..., min_length=10)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: object) -> object:
        return normalize_compliance_status(value)

    @field_validator("contract_quote", "policy_quote", mode="before")
    @classmethod
    def coerce_quotes(cls, value: object) -> str:
        return coerce_quote_field(value)


class BatchComplianceItem(BaseModel):
    """One category result inside a batched compliance LLM response."""

    category_id: str
    status: ComplianceStatus
    severity: Severity = Severity.INFO
    contract_quote: str = ""
    policy_quote: str = ""
    rationale: str = Field(..., min_length=10)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    needs_policy: bool = False
    policy_topic: str = ""
    suggested_search_queries: list[str] = Field(default_factory=list)

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: object) -> object:
        return normalize_compliance_status(value)

    @field_validator("contract_quote", "policy_quote", mode="before")
    @classmethod
    def coerce_quotes(cls, value: object) -> str:
        return coerce_quote_field(value)


class BatchComplianceLLMResult(BaseModel):
    """Structured batch output for hybrid Pass 1 / Pass 2."""

    items: list[BatchComplianceItem] = Field(..., min_length=1)
