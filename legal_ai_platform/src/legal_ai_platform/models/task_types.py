"""Supported agent task types for request routing."""

from __future__ import annotations

from enum import Enum


class TaskType(str, Enum):
    """Legal AI agent task types.

    Pass one or more values in ``task_type`` (priority order). If omitted, the
    classifier selects an agent from the query text.
    """

    RESEARCH = "research"
    CONTRACT = "contract"
    SUMMARY = "summary"
    DRAFTING = "drafting"
    LITIGATION = "litigation"
    COMPLIANCE = "compliance"
    PROPERTY = "property"
    IP = "ip"
    TRANSLATION = "translation"
