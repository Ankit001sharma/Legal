"""LLM access for compliance review (OpenAI-compatible / on-prem)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from dataclasses import dataclass, field
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from review_agent.errors import LLMUnavailableError
from review_agent.observability.metrics import record_llm_call
from review_agent.resilience.circuit_breaker import get_llm_breaker

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass
class _ReviewLLMLimiter:
    semaphore: asyncio.Semaphore
    rate_limit_events: int = field(default=0)


_limiter: _ReviewLLMLimiter | None = None


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def reset_llm_limiter() -> None:
    """Reset global limiter (tests and settings reload)."""
    global _limiter  # noqa: PLW0603
    _limiter = None


def get_llm_limiter_stats() -> dict[str, int]:
    """Observability hook for review artifact ops (P1)."""
    if _limiter is None:
        return {"rate_limit_events": 0}
    return {"rate_limit_events": _limiter.rate_limit_events}


def _get_limiter() -> _ReviewLLMLimiter:
    global _limiter  # noqa: PLW0603
    if _limiter is None:
        from review_agent.config import get_settings

        cfg = get_settings()
        _limiter = _ReviewLLMLimiter(
            semaphore=asyncio.Semaphore(max(1, cfg.llm_global_concurrency))
        )
    return _limiter


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Detect provider rate limits (Mistral 1300, HTTP 429, etc.)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    depth = 0
    while current is not None and id(current) not in seen and depth < 4:
        seen.add(id(current))
        depth += 1
        try:
            import httpx

            if isinstance(current, httpx.HTTPStatusError):
                if current.response.status_code == 429:
                    return True
        except ImportError:
            pass
        text = str(current).lower()
        if (
            "429" in text
            or "rate limit" in text
            or "rate_limited" in text
            or '"code":"1300"' in text
            or "'code':'1300'" in text
            or '"code": "1300"' in text
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def get_review_model(*, temperature: float = 0.0, max_tokens: int | None = None) -> BaseChatModel:
    """Create a chat model using the same env vars as the research agent."""
    from langchain.chat_models import init_chat_model

    role = _env("COMPLIANCE_LLM_ROLE", "reasoning")
    model = _env(f"LLM_MODEL_{role.upper()}") or _env("LLM_MODEL") or "gpt-4o-mini"

    kwargs: dict[str, Any] = {"temperature": temperature}
    base_url = _env("LLM_BASE_URL")
    api_key = _env("LLM_API_KEY") or _env("OPENAI_API_KEY") or _env("MISTRAL_API_KEY")
    provider = _env("LLM_PROVIDER")

    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    if provider:
        kwargs["model_provider"] = "openai" if provider == "nvidia" and base_url else provider
    elif ":" not in model:
        kwargs["model_provider"] = "openai"

    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens

    return init_chat_model(model=model, **kwargs)


def _extract_json_payload(text: str) -> Any:
    """Parse JSON from model output; tolerate fences, arrays, and extra trailing objects."""
    stripped = text.strip()
    fence = re.search(r"```(?:json)?\s*([\[\{].*[\]\}])\s*```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        if "Extra data" not in str(exc):
            raise
        decoder = json.JSONDecoder()
        items: list[Any] = []
        idx = 0
        length = len(stripped)
        while idx < length:
            while idx < length and stripped[idx].isspace():
                idx += 1
            if idx >= length:
                break
            try:
                obj, end = decoder.raw_decode(stripped, idx)
            except json.JSONDecodeError:
                break
            items.append(obj)
            idx = end
        if not items:
            raise
        if len(items) == 1:
            return items[0]
        return {"items": items}


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model output (legacy callers)."""
    payload = _extract_json_payload(text)
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return {"items": payload}
    raise ValueError("expected JSON object")


async def _invoke_once(
    model: BaseChatModel,
    schema: type[T],
    *,
    system: str,
    user: str,
) -> T:
    """Single LLM attempt: structured output, then JSON parse fallback."""
    try:
        structured = model.with_structured_output(schema)
        result = await structured.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )
        if isinstance(result, schema):
            return result
        return schema.model_validate(result)
    except Exception as exc:  # noqa: BLE001
        if _is_rate_limit_error(exc):
            raise
        logger.debug("structured output failed, falling back to JSON parse: %s", exc)

    try:
        response = await model.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )
    except Exception as exc:  # noqa: BLE001
        if _is_rate_limit_error(exc):
            raise
        raise

    content = getattr(response, "content", "")
    if not isinstance(content, str):
        raise ValueError("LLM returned non-text content")
    data = _extract_json_payload(content)
    return schema.model_validate(data)


async def invoke_structured(
    model: BaseChatModel,
    schema: type[T],
    *,
    system: str,
    user: str,
) -> T:
    """Invoke model with global concurrency cap and rate-limit retries."""
    from review_agent.config import get_settings

    breaker = get_llm_breaker()
    if not breaker.allow():
        record_llm_call("invoke_structured", "circuit_open")
        raise LLMUnavailableError("circuit_open:llm — LLM breaker is open")

    cfg = get_settings()
    limiter = _get_limiter()

    async with limiter.semaphore:
        last_exc: BaseException | None = None
        max_attempts = max(0, cfg.llm_rate_limit_max_retries) + 1
        for attempt in range(max_attempts):
            try:
                result = await _invoke_once(
                    model,
                    schema,
                    system=system,
                    user=user,
                )
                breaker.record_success()
                record_llm_call("invoke_structured", "ok")
                return result
            except Exception as exc:  # noqa: BLE001
                if not _is_rate_limit_error(exc):
                    breaker.record_failure()
                    record_llm_call("invoke_structured", "error")
                    raise
                last_exc = exc
                limiter.rate_limit_events += 1
                if attempt >= max_attempts - 1:
                    logger.warning(
                        "LLM rate limit retries exhausted (%s attempts): %s",
                        max_attempts,
                        exc,
                    )
                    record_llm_call("invoke_structured", "rate_limited")
                    raise
                delay = min(
                    cfg.llm_rate_limit_backoff_base_seconds * (2**attempt),
                    cfg.llm_rate_limit_backoff_max_seconds,
                ) + random.uniform(0, 0.5)
                logger.warning(
                    "LLM rate limited (attempt %s/%s), sleeping %.1fs",
                    attempt + 1,
                    max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("invoke_structured retry loop exited without result")
