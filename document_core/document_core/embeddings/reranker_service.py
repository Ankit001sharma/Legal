"""Cross-encoder reranking for retrieval precision (optional dependency)."""

from __future__ import annotations

import logging
from functools import lru_cache

from document_core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_cross_encoder(model_name: str):
    from sentence_transformers import CrossEncoder

    logger.info("loading reranker model: %s", model_name)
    return CrossEncoder(model_name)


def reranker_available() -> bool:
    settings = get_settings()
    if not settings.reranker_enabled:
        return False
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False
    return True


def score_query_passages(
    query: str,
    passages: list[str],
) -> list[float] | None:
    """Return cross-encoder scores aligned with passages, or None on failure."""
    if not passages:
        return []
    if not reranker_available():
        return None
    q = (query or "").strip()
    if not q:
        return None
    if not reranker_available():
        return None

    settings = get_settings()
    try:
        model = _load_cross_encoder(settings.reranker_model)
        pairs = [(q, passage or "") for passage in passages]
        raw = model.predict(pairs)
        return [float(score) for score in raw]
    except Exception as exc:  # noqa: BLE001
        logger.warning("cross-encoder rerank failed: %s", exc)
        return None
