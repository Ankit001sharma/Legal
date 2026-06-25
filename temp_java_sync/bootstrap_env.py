"""Load temp_java_sync/.env into os.environ before review runs."""

from __future__ import annotations

import os
from pathlib import Path

_REVIEW_ENV_PREFIXES = (
    "DISCOVERY_",
    "SECTION_",
    "COMPARE_",
    "RETRIEVAL_",
    "GUARD_",
    "LLM_",
    "GAP_",
    "FINAL_",
    "REVIEW_",
    "ENFORCE_",
    "FINDING_",
    "PLAYBOOK_",
    "GROUNDING_",
    "RERANKER_",
)


def _should_load_review_env_key(key: str) -> bool:
    if key in {"MISTRAL_API_KEY"}:
        return True
    return any(key.startswith(prefix) for prefix in _REVIEW_ENV_PREFIXES)


def load_env() -> Path:
    root = Path(__file__).resolve().parent
    env_path = root / ".env"
    example = root / ".env.example"
    target = env_path if env_path.is_file() else example
    if not target.is_file():
        return root

    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if key and value:
            os.environ.setdefault(key, value)

    review_env = root.parent / "review" / "review_agent" / ".env"
    review_example = root.parent / "review" / "review_agent" / ".env.example"
    for source in (review_env, review_example):
        if not source.is_file():
            continue
        for line in source.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            if not key or not _should_load_review_env_key(key) or not value:
                continue
            if not os.environ.get(key):
                os.environ[key] = value
    return root


def setup_pythonpath() -> None:
    import sys

    legal = Path(__file__).resolve().parent.parent
    paths = [
        str(legal / "document_core"),
        str(legal / "review" / "review_agent"),
        str(legal / "Legal ai"),
        str(legal / "temp_java_sync"),
    ]
    existing = os.environ.get("PYTHONPATH", "")
    parts = [p for p in paths + ([existing] if existing else []) if p]
    os.environ["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(parts))
    for path in paths:
        if path not in sys.path:
            sys.path.insert(0, path)
