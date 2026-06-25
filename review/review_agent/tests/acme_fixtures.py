"""Acme NDA fixture loaders for Phase 38 integration tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from document_core.schemas.chunk import IngestSectionInput

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_json(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def load_acme_contract() -> dict[str, Any]:
    return _load_json("acme_nda_contract.json")


def load_acme_policies() -> dict[str, dict[str, Any]]:
    return _load_json("acme_nda_policies.json")


def acme_contract_sections() -> list[IngestSectionInput]:
    contract = load_acme_contract()
    return [
        IngestSectionInput(
            section_id=str(section["section_id"]),
            title=str(section.get("title") or ""),
            text=str(section["text"]),
        )
        for section in contract["sections"]
    ]


def acme_policy_specs() -> dict[str, dict[str, Any]]:
    return load_acme_policies()
