"""Review-agent logging helpers (Phase 31)."""

from __future__ import annotations

import json
import logging

from review_agent.observability.context import context_dict


class ReviewContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in context_dict().items():
            setattr(record, key, value)
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "tenant_id": getattr(record, "tenant_id", ""),
            "thread_id": getattr(record, "thread_id", ""),
            "node": getattr(record, "node", ""),
        }
        return json.dumps(payload, ensure_ascii=False)


def configure_review_logging(*, json_logs: bool = False) -> None:
    log = logging.getLogger("review_agent")
    if getattr(log, "_review_obs_configured", False):
        return
    log.addFilter(ReviewContextFilter())
    if json_logs:
        formatter = _JsonFormatter()
        if not log.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(formatter)
            log.addHandler(handler)
        else:
            for handler in log.handlers:
                handler.setFormatter(formatter)
    log._review_obs_configured = True  # type: ignore[attr-defined]
