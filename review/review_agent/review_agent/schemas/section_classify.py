"""Section → policy category classification (Phase 10)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SectionCategoryResult(BaseModel):
    section_id: str
    categories: list[str] = Field(default_factory=list)
    query_terms: list[str] = Field(default_factory=list)
    classify_warning: str | None = None
    substantive: bool = True
    related_section_ids: list[str] = Field(default_factory=list)


class SectionCategoryLLMResult(BaseModel):
    section_id: str | None = None
    categories: list[str] = Field(default_factory=list)
    query_terms: list[str] = Field(default_factory=list)


class BatchSectionCategoryLLMResult(BaseModel):
    items: list[SectionCategoryResult] = Field(default_factory=list)
