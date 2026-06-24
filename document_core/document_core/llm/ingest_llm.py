"""Minimal OpenAI-compatible structured JSON for ingest-time tagging."""

from __future__ import annotations

import json
import logging
import os
from typing import TypeVar

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def llm_api_key_available() -> bool:
    return bool(os.environ.get("LLM_API_KEY") or os.environ.get("MISTRAL_API_KEY"))


def _api_key() -> str:
    key = os.environ.get("LLM_API_KEY") or os.environ.get("MISTRAL_API_KEY") or ""
    if not key:
        raise RuntimeError("LLM_API_KEY or MISTRAL_API_KEY is required for category tagger LLM mode")
    return key


def _base_url() -> str:
    return (os.environ.get("LLM_BASE_URL") or "https://api.mistral.ai/v1").rstrip("/")


async def invoke_structured_json(
    *,
    model: str,
    system: str,
    user: str,
    schema: type[T],
    temperature: float = 0.0,
) -> T:
    """Call chat completions and parse JSON into a Pydantic model."""
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{_base_url()}/chat/completions",
            headers={"Authorization": f"Bearer {_api_key()}"},
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
    content = body["choices"][0]["message"]["content"]
    data = json.loads(content)
    return schema.model_validate(data)
