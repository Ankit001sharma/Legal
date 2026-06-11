"""Streamlit chatbot UI for the Legal AI Research Agent."""

from __future__ import annotations

import json
from typing import Any

import httpx
import streamlit as st

DEFAULT_PLATFORM_URL = "http://localhost:8080"
TIMEOUT_SECONDS = 300

st.set_page_config(
    page_title="Legal AI Research",
    page_icon="⚖️",
    layout="centered",
)


def _api(
    base_url: str,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: int = TIMEOUT_SECONDS,
) -> tuple[int | None, Any, str | None]:
    url = f"{base_url.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=timeout) as client:
            if method == "GET":
                response = client.get(url)
            else:
                response = client.post(url, json=json_body)
        try:
            payload: Any = response.json()
        except json.JSONDecodeError:
            payload = response.text
        return response.status_code, payload, None
    except httpx.ConnectError:
        return None, None, f"Cannot connect to {url}. Is the platform gateway running?"
    except httpx.TimeoutException:
        return None, None, f"Request timed out after {timeout}s."
    except httpx.HTTPError as exc:
        return None, None, str(exc)


def _init_state() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("thread_id", None)
    st.session_state.setdefault("awaiting_input", False)


def _reset_chat() -> None:
    st.session_state["messages"] = []
    st.session_state["thread_id"] = None
    st.session_state["awaiting_input"] = False


def _run_query(platform_url: str, prompt: str) -> None:
    body: dict[str, Any] = {"query": prompt, "max_results": 10}
    if st.session_state.get("thread_id"):
        body["thread_id"] = st.session_state["thread_id"]

    status, payload, error = _api(
        platform_url, "POST", "/query", json_body=body, timeout=TIMEOUT_SECONDS
    )

    if error:
        return _push_assistant(f"⚠️ {error}", success=False)
    if status != 200 or not isinstance(payload, dict):
        return _push_assistant(f"⚠️ Unexpected response (HTTP {status}).", success=False)

    if payload.get("thread_id"):
        st.session_state["thread_id"] = payload["thread_id"]
    st.session_state["awaiting_input"] = bool(payload.get("awaiting_input"))

    output = (payload.get("output") or "").strip()
    if payload.get("awaiting_input"):
        text = output or "Could you share a bit more detail so I can refine the research?"
        _push_assistant(text, success=True, clarifying=True, meta=payload)
    elif payload.get("success"):
        text = output or (
            "The request completed but returned no text. Check the gateway logs."
        )
        _push_assistant(text, success=True, meta=payload)
    else:
        text = payload.get("error") or "Research failed."
        _push_assistant(f"⚠️ {text}", success=False, meta=payload)


def _push_assistant(
    text: str,
    *,
    success: bool,
    clarifying: bool = False,
    meta: dict[str, Any] | None = None,
) -> None:
    st.session_state["messages"].append(
        {
            "role": "assistant",
            "content": text,
            "success": success,
            "clarifying": clarifying,
            "meta": meta or {},
        }
    )


def _render_assistant_extras(message: dict[str, Any]) -> None:
    meta = message.get("meta") or {}
    if not meta:
        return
    bits = []
    if meta.get("agent"):
        bits.append(f"**agent:** {meta['agent']}")
    if meta.get("task_type"):
        bits.append(f"**task_type:** {meta['task_type']}")
    if bits:
        st.caption(" · ".join(bits))
    if meta.get("artifacts"):
        with st.expander("Artifacts"):
            st.json(meta["artifacts"])
    if meta.get("events"):
        with st.expander("Events"):
            st.json(meta["events"])


_init_state()

with st.sidebar:
    st.header("⚖️ Legal AI Research")
    platform_url = st.text_input(
        "Platform gateway URL",
        value=DEFAULT_PLATFORM_URL,
        key="platform_url",
    )

    if st.button("Check health", use_container_width=True, key="health_btn"):
        status, payload, error = _api(platform_url, "GET", "/health", timeout=10)
        if error:
            st.error(error)
        elif status == 200 and isinstance(payload, dict):
            st.success(
                f"{payload.get('service')} v{payload.get('version')} — "
                f"{payload.get('status')}"
            )
        else:
            st.warning(f"Unexpected response (HTTP {status})")

    if st.button("🗑️ New chat", use_container_width=True, key="new_chat_btn"):
        _reset_chat()
        st.rerun()

    st.divider()
    if st.session_state.get("thread_id"):
        st.caption(f"Session: `{st.session_state['thread_id']}`")
    st.caption(
        "Start the platform gateway with:\n\n"
        "```\nuvicorn legal_ai_platform.gateway.app:app --port 8080\n```"
    )

st.title("Legal Research Assistant")

if not st.session_state["messages"]:
    st.caption(
        "Ask a legal research question and I'll investigate using the research agent. "
        "I'll keep context across follow-up questions."
    )

for message in st.session_state["messages"]:
    avatar = "🧑" if message["role"] == "user" else "⚖️"
    with st.chat_message(message["role"], avatar=avatar):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            _render_assistant_extras(message)

placeholder = (
    "Add the clarifying details…"
    if st.session_state.get("awaiting_input")
    else "Ask a legal research question…"
)

if prompt := st.chat_input(placeholder):
    st.session_state["messages"].append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar="🧑"):
        st.markdown(prompt)

    with st.chat_message("assistant", avatar="⚖️"):
        with st.spinner("Researching… this can take a few minutes."):
            _run_query(platform_url, prompt)
    st.rerun()
