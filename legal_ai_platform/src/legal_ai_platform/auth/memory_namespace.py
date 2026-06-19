"""Re-export memory namespace types from the research agent package."""

from deep_research_from_scratch.memory_namespace import (
    MemoryNamespace,
    MemoryPaths,
    UserRole as MemoryUserRole,
    apply_config_namespace,
    get_active_namespace,
    resolve_memory_paths,
    set_memory_namespace,
)

__all__ = [
    "MemoryNamespace",
    "MemoryPaths",
    "MemoryUserRole",
    "apply_config_namespace",
    "get_active_namespace",
    "resolve_memory_paths",
    "set_memory_namespace",
]
