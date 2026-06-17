"""Unified session memory (platform-owned, all agents)."""

from legal_ai_platform.session.file_store import SessionFileStore
from legal_ai_platform.session.memory_bridge import MemoryBridge
from legal_ai_platform.session.models import MatterSnapshot, SessionState, Turn
from legal_ai_platform.session.research_cleanup import delete_legacy_research_session_files
from legal_ai_platform.session.service import SessionService

__all__ = [
    "MatterSnapshot",
    "MemoryBridge",
    "SessionFileStore",
    "SessionService",
    "SessionState",
    "Turn",
]
