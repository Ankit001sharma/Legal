"""Structured LLM batch output for policy section category tagging."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SectionCategoryTag(BaseModel):
    section_id: str
    categories: list[str] = Field(default_factory=list)


class BatchSectionCategoryTagResult(BaseModel):
    items: list[SectionCategoryTag] = Field(default_factory=list)
