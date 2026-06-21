"""P1-4.3: Category alias normalization for metadata-filter retrieval."""

from document_core.schemas.taxonomy import normalize_categories


def test_category_alias_indemnification() -> None:
    assert normalize_categories(["indemnification"]) == ["indemnity"]


def test_category_alias_data_protection() -> None:
    assert normalize_categories(["data_protection"]) == ["privacy"]


def test_category_alias_liability_phrase() -> None:
    assert normalize_categories(["limitation_of_liability"]) == ["liability"]


def test_category_dedupe_after_alias() -> None:
    assert normalize_categories(["indemnification", "indemnity"]) == ["indemnity"]
