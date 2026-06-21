"""Tests for policy category taxonomy."""

from document_core.schemas.taxonomy import normalize_categories


def test_normalize_categories_dedupes_and_lowercases():
    assert normalize_categories(["Liability", " liability ", "Privacy"]) == [
        "liability",
        "privacy",
    ]


def test_normalize_empty():
    assert normalize_categories([]) == []
    assert normalize_categories(None) == []


def test_esg_alias_maps_to_environment() -> None:
    assert normalize_categories(["esg"]) == ["environment"]


def test_forced_labor_alias_maps_to_human_rights() -> None:
    assert normalize_categories(["forced_labor", "labor"]) == ["human_rights", "labor"]


def test_minerals_alias() -> None:
    assert normalize_categories(["responsible_minerals"]) == ["minerals"]
