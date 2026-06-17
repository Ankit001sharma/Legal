"""Load generic review dimensions (search intents only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_DIMENSIONS_PATH = Path(__file__).resolve().parent.parent / "dimensions" / "review_dimensions.yaml"


def load_dimensions(path: Path | None = None) -> dict[str, dict[str, Any]]:
    target = path or _DIMENSIONS_PATH
    with target.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("review_dimensions.yaml must be a mapping")
    return data
