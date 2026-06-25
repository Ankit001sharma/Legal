"""Per-review correlation context (Phase 31)."""

from __future__ import annotations

from contextvars import ContextVar

_tenant_id: ContextVar[str] = ContextVar("tenant_id", default="")
_thread_id: ContextVar[str] = ContextVar("thread_id", default="")
_node: ContextVar[str] = ContextVar("node", default="")


def bind_review_context(*, tenant_id: str, thread_id: str) -> None:
    _tenant_id.set(tenant_id)
    _thread_id.set(thread_id)


def set_current_node(name: str) -> None:
    _node.set(name)


def clear_review_context() -> None:
    _tenant_id.set("")
    _thread_id.set("")
    _node.set("")


def context_dict() -> dict[str, str]:
    return {
        "tenant_id": _tenant_id.get(),
        "thread_id": _thread_id.get(),
        "node": _node.get(),
    }
