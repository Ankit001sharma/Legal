"""Database engine and session helpers."""

from __future__ import annotations

from legal_ai_platform.db.models import Base as AuthBase
from legal_ai_platform.db.session import get_engine, get_session, get_session_factory

from db.models import CrawlerBase


def init_db(database_url: str) -> None:
    """Create auth and crawler tables (dev/bootstrap; production uses migrations)."""
    engine = get_engine(database_url)
    AuthBase.metadata.create_all(engine)
    CrawlerBase.metadata.create_all(engine)


__all__ = ["get_engine", "get_session", "get_session_factory", "init_db"]
