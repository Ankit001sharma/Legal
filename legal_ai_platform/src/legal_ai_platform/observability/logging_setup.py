"""Central logging configuration for the Legal AI Platform."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_PLATFORM_ROOT = Path(__file__).resolve().parent.parent.parent.parent
LOG_DIR = _PLATFORM_ROOT / "logs"
LOG_FILE = LOG_DIR / "platform.log"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

_configured = False


class SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that tolerates Windows file-lock errors on rollover."""

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except PermissionError:
            pass


def truncate(text: str | None, max_len: int = 200) -> str:
    """Truncate text for safe logging (queries, snippets)."""
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def sanitize_for_log(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *data* with sensitive fields redacted."""
    sanitized = dict(data)
    context = sanitized.get("context")
    if isinstance(context, dict) and "auth_token" in context:
        sanitized["context"] = {**context, "auth_token": "***"}
    return sanitized


def configure_logging(log_level: str = "INFO") -> None:
    """Configure platform logging once at application startup."""
    global _configured
    if _configured:
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, log_level.upper(), logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(level)

        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setFormatter(fmt)
        stdout_handler.setLevel(level)
        root.addHandler(stdout_handler)

        file_handler = SafeRotatingFileHandler(
            LOG_FILE,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    else:
        root.setLevel(level)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger."""
    return logging.getLogger(name)
