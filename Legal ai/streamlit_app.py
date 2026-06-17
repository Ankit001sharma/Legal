"""Streamlit chatbot UI for the Legal AI Research Agent."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
import streamlit as st
import streamlit.components.v1 as components

from chat_history import (
    delete_chat,
    group_chats_by_period,
    list_chats,
    load_chat,
    new_chat_id,
    save_chat,
)

DEFAULT_PLATFORM_URL = "http://localhost:8080"
TIMEOUT_SECONDS = int(os.environ.get("STREAMLIT_QUERY_TIMEOUT", "900"))
WAIT_MESSAGE = "Researching… this can take a few minutes."

RESEARCH_MODES = {
    "Normal Research": "normal",
    "Deep Research": "deep",
}
RESEARCH_MODE_DESCRIPTIONS = {
    "Normal Research": "Fast legal answer · 2-3 retrieval rounds · concise response",
    "Deep Research": "Exhaustive legal memo · 12+ source fetches · 5,000-10,000 word output",
}

st.set_page_config(
    page_title="Legal AI Research",
    page_icon="⚖️",
    layout="centered",
)


# ── HTTP helper ───────────────────────────────────────────────────────────────

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


# ── Session state ─────────────────────────────────────────────────────────────

def _init_state() -> None:
    st.session_state.setdefault("messages", [])
    st.session_state.setdefault("thread_id", None)
    st.session_state.setdefault("active_chat_id", None)
    st.session_state.setdefault("awaiting_input", False)
    st.session_state.setdefault("suggested_followups", [])
    st.session_state.setdefault("pending_followup", None)
    st.session_state.setdefault("research_directions", [])
    st.session_state.setdefault("research_mode", "Normal Research")
    st.session_state.setdefault("pending_chat_id", None)
    st.session_state.setdefault("pending_chat_delete", None)


def _ensure_chat_id() -> str:
    chat_id = st.session_state.get("active_chat_id")
    if not chat_id:
        chat_id = new_chat_id()
        st.session_state["active_chat_id"] = chat_id
    return chat_id


def _persist_current_chat() -> None:
    messages = st.session_state.get("messages") or []
    if not messages:
        return
    chat_id = _ensure_chat_id()
    existing = load_chat(chat_id)
    save_chat(
        chat_id=chat_id,
        messages=messages,
        thread_id=st.session_state.get("thread_id"),
        research_mode=st.session_state.get("research_mode", "Normal Research"),
        awaiting_input=bool(st.session_state.get("awaiting_input")),
        created_at=existing.get("created_at") if existing else None,
    )


def _restore_followups_from_messages(messages: list[dict[str, Any]]) -> list[str]:
    for message in reversed(messages):
        if (
            message.get("role") == "assistant"
            and message.get("success")
            and not message.get("clarifying")
        ):
            return _parse_followup_questions(message.get("content", ""))
    return []


def _load_chat_into_state(chat_id: str) -> None:
    data = load_chat(chat_id)
    if not data:
        return

    st.session_state["active_chat_id"] = chat_id
    st.session_state["messages"] = data.get("messages") or []
    st.session_state["thread_id"] = data.get("thread_id")
    st.session_state["research_mode"] = data.get("research_mode", "Normal Research")
    st.session_state["research_mode_radio"] = st.session_state["research_mode"]
    st.session_state["awaiting_input"] = bool(data.get("awaiting_input"))
    st.session_state["research_directions"] = []
    st.session_state["pending_followup"] = None
    if st.session_state["awaiting_input"]:
        st.session_state["suggested_followups"] = []
    else:
        st.session_state["suggested_followups"] = _restore_followups_from_messages(
            st.session_state["messages"]
        )


def _reset_chat(*, persist: bool = True) -> None:
    if persist:
        _persist_current_chat()
    st.session_state["messages"] = []
    st.session_state["thread_id"] = None
    st.session_state["active_chat_id"] = None
    st.session_state["awaiting_input"] = False
    st.session_state["suggested_followups"] = []
    st.session_state["pending_followup"] = None
    st.session_state["research_directions"] = []
    # Preserve the selected research mode across new chats


# ── Output display sanitization ───────────────────────────────────────────────

_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0000FE00-\U0000FE0F"
    "\U0000200D"
    "]+",
    flags=re.UNICODE,
)


def _sanitize_output_text(text: str) -> str:
    """Remove emojis from text shown in the main chat/output area."""
    cleaned = text.replace("⚠️", "").replace("✅", "")
    cleaned = _EMOJI_RE.sub("", cleaned)
    lines = []
    for line in cleaned.splitlines():
        lines.append(re.sub(r"[ \t]+", " ", line).rstrip())
    return "\n".join(lines)


# ── Follow-up question parsing ────────────────────────────────────────────────

def _parse_followup_questions(text: str) -> list[str]:
    """Extract numbered follow-up questions from the Suggested Follow-up Queries section."""
    questions: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"#+\s*suggested follow.up", stripped, re.IGNORECASE):
            in_section = True
            continue
        if in_section and re.match(r"^#{1,3}\s", stripped) and not re.match(r"#+\s*suggested follow.up", stripped, re.IGNORECASE):
            break
        if in_section:
            m = re.match(r"^(\d+)[.)]\s+(.+)", stripped)
            if m:
                question = m.group(2).strip()
                if len(question) > 15:
                    questions.append(question)
    return questions[:5]


# ── Query execution ───────────────────────────────────────────────────────────

def _run_query(platform_url: str, prompt: str) -> None:
    selected_label = st.session_state.get("research_mode", "Normal Research")
    mode_value = RESEARCH_MODES.get(selected_label, "normal")
    body: dict[str, Any] = {
        "query": prompt,
        "task_type": "research",
        "max_results": 10,
        "mode": mode_value,
    }
    if st.session_state.get("thread_id"):
        body["thread_id"] = st.session_state["thread_id"]

    status, payload, error = _api(
        platform_url, "POST", "/query", json_body=body, timeout=TIMEOUT_SECONDS
    )

    if error:
        return _push_assistant(f"Error: {error}", success=False)
    if status != 200 or not isinstance(payload, dict):
        return _push_assistant(f"Error: Unexpected response (HTTP {status}).", success=False)

    if payload.get("thread_id"):
        st.session_state["thread_id"] = payload["thread_id"]
    st.session_state["awaiting_input"] = bool(payload.get("awaiting_input"))
    st.session_state["research_directions"] = payload.get("research_directions") or []

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
        _push_assistant(f"Error: {text}", success=False, meta=payload)


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
    if success and not clarifying:
        followups = _parse_followup_questions(text)
        st.session_state["suggested_followups"] = followups
    elif clarifying:
        st.session_state["suggested_followups"] = []
    _persist_current_chat()


# ── Export helpers ────────────────────────────────────────────────────────────

def _format_conversation_markdown(messages: list[dict[str, Any]]) -> str:
    parts = ["# Legal Research Conversation\n"]
    for message in messages:
        role = "You" if message["role"] == "user" else "Assistant"
        parts.append(f"## {role}\n\n{message.get('content', '').strip()}\n")
    parts.append(
        f"\n---\n*Exported {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*"
    )
    return "\n".join(parts)


_COPY_ICON = (
    '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>'
    '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>'
)
_DOWNLOAD_ICON = (
    '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>'
    '<polyline points="7 10 12 15 17 10"></polyline>'
    '<line x1="12" y1="15" x2="12" y2="3"></line>'
)


def _render_icon_toolbar(
    text: str,
    *,
    action_key: str,
    download_filename: str,
) -> None:
    """Compact copy/download icon row shown below research content."""
    content = text.strip()
    if not content:
        return

    escaped_text = json.dumps(content)
    escaped_filename = json.dumps(download_filename)
    components.html(
        f"""
        <style>
          .msg-actions {{
            display: flex;
            align-items: center;
            gap: 2px;
            margin-top: 2px;
          }}
          .msg-actions button {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 32px;
            height: 32px;
            border: none;
            border-radius: 8px;
            background: transparent;
            color: rgba(49, 51, 63, 0.6);
            cursor: pointer;
            padding: 0;
            transition: background 0.15s ease, color 0.15s ease;
          }}
          .msg-actions button:hover {{
            background: rgba(49, 51, 63, 0.08);
            color: rgba(49, 51, 63, 0.95);
          }}
          .msg-actions svg {{
            width: 18px;
            height: 18px;
            stroke: currentColor;
            fill: none;
            stroke-width: 1.75;
            stroke-linecap: round;
            stroke-linejoin: round;
          }}
        </style>
        <div class="msg-actions">
          <button id="copy-{action_key}" title="Copy" aria-label="Copy">
            <svg viewBox="0 0 24 24">{_COPY_ICON}</svg>
          </button>
          <button id="download-{action_key}" title="Download" aria-label="Download">
            <svg viewBox="0 0 24 24">{_DOWNLOAD_ICON}</svg>
          </button>
        </div>
        <script>
        (function() {{
          var text = {escaped_text};
          var filename = {escaped_filename};
          var copyBtn = document.getElementById("copy-{action_key}");
          var downloadBtn = document.getElementById("download-{action_key}");

          copyBtn.addEventListener("click", function() {{
            navigator.clipboard.writeText(text).then(function() {{
              copyBtn.style.color = "rgb(19, 124, 87)";
              setTimeout(function() {{ copyBtn.style.color = ""; }}, 1500);
            }});
          }});

          downloadBtn.addEventListener("click", function() {{
            var blob = new Blob([text], {{ type: "text/markdown" }});
            var url = URL.createObjectURL(blob);
            var link = document.createElement("a");
            link.href = url;
            link.download = filename;
            link.click();
            URL.revokeObjectURL(url);
          }});
        }})();
        </script>
        """,
        height=38,
    )


def _render_research_actions(content: str, action_key: str) -> None:
    _render_icon_toolbar(
        content,
        action_key=action_key,
        download_filename=f"legal_research_{action_key}.md",
    )


def _render_chat_history_sidebar() -> None:
    """ChatGPT-style previous chats list in the sidebar."""
    chats = list_chats()
    if not chats:
        st.caption("No previous chats yet.")
        return

    active_id = st.session_state.get("active_chat_id")
    for section_label, section_chats in group_chats_by_period(chats):
        st.caption(section_label)
        for chat in section_chats:
            chat_id = chat["id"]
            title = chat.get("title") or "New chat"
            label = title if len(title) <= 42 else title[:39] + "…"
            is_active = chat_id == active_id
            col_open, col_delete = st.columns([5, 1], gap="small")
            with col_open:
                if st.button(
                    label,
                    key=f"open_chat_{chat_id}",
                    use_container_width=True,
                    type="primary" if is_active else "secondary",
                ):
                    if not is_active:
                        st.session_state["pending_chat_id"] = chat_id
            with col_delete:
                if st.button("🗑", key=f"delete_chat_{chat_id}", help="Delete chat"):
                    st.session_state["pending_chat_delete"] = chat_id


# ── Assistant message extras ──────────────────────────────────────────────────

def _render_assistant_extras(message: dict[str, Any]) -> None:
    meta = message.get("meta") or {}
    if not meta:
        return
    bits = []
    if meta.get("agent"):
        bits.append(f"**agent:** {meta['agent']}")
    if meta.get("task_type"):
        bits.append(f"**task_type:** {meta['task_type']}")
    # Show the mode that was used for this particular response
    artifacts = meta.get("artifacts") or {}
    mode_used = artifacts.get("mode") or (artifacts.get("research") or {}).get("mode")
    if not mode_used and meta.get("artifacts"):
        mode_used = meta["artifacts"].get("mode")
    if mode_used:
        bits.append(f"**mode:** {mode_used}")
    if bits:
        st.caption(" · ".join(bits))
    if meta.get("artifacts"):
        with st.expander("Artifacts"):
            st.json(meta["artifacts"])
    if meta.get("events"):
        with st.expander("Events"):
            st.json(meta["events"])


# ── App layout ────────────────────────────────────────────────────────────────

_init_state()

# Handle deferred chat switch/delete before rendering sidebar actions.
if st.session_state.get("pending_chat_delete"):
    delete_id = st.session_state.pop("pending_chat_delete")
    if delete_id == st.session_state.get("active_chat_id"):
        _reset_chat(persist=False)
    delete_chat(delete_id)
    st.rerun()

if st.session_state.get("pending_chat_id"):
    switch_id = st.session_state.pop("pending_chat_id")
    if switch_id != st.session_state.get("active_chat_id"):
        _persist_current_chat()
        _load_chat_into_state(switch_id)
    st.rerun()

with st.sidebar:
    st.header("⚖️ Legal AI Research")

    if st.button("＋ New chat", use_container_width=True, key="new_chat_btn", type="primary"):
        _reset_chat()
        st.rerun()

    st.markdown("**Chats**")
    _render_chat_history_sidebar()
    st.divider()

    # ── Research mode selector ────────────────────────────────────────────────
    st.markdown("**Research Mode**")
    selected_mode = st.radio(
        label="Research Mode",
        options=list(RESEARCH_MODES.keys()),
        index=list(RESEARCH_MODES.keys()).index(
            st.session_state.get("research_mode", "Normal Research")
        ),
        key="research_mode_radio",
        label_visibility="collapsed",
    )
    st.session_state["research_mode"] = selected_mode
    st.caption(RESEARCH_MODE_DESCRIPTIONS.get(selected_mode, ""))
    st.divider()

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

    messages = st.session_state.get("messages", [])
    if messages:
        st.divider()
        st.caption("Export this session")
        _render_icon_toolbar(
            _format_conversation_markdown(messages),
            action_key="conversation",
            download_filename="legal_research_conversation.md",
        )

    st.divider()
    if st.session_state.get("thread_id"):
        st.caption(f"Session: `{st.session_state['thread_id']}`")
    st.caption(
        "Start the platform gateway with:\n\n"
        "```\nuvicorn legal_ai_platform.gateway.app:app --port 8080\n```"
    )

st.title("Legal Research Assistant")

# Show active research mode as a status badge below the title
_active_mode = st.session_state.get("research_mode", "Normal Research")
_mode_icon = "⚡" if _active_mode == "Normal Research" else "🔬"
st.markdown(
    f"{_mode_icon} **Active mode:** {_active_mode} — "
    f"{RESEARCH_MODE_DESCRIPTIONS.get(_active_mode, '')}"
)

if not st.session_state["messages"]:
    st.caption(
        "Ask a legal research question and I'll investigate using the research agent. "
        "I'll keep context across follow-up questions."
    )

# ── Render existing messages ──────────────────────────────────────────────────

for idx, message in enumerate(st.session_state["messages"]):
    with st.chat_message(message["role"]):
        st.markdown(_sanitize_output_text(message["content"]))
        if message["role"] == "assistant":
            _render_assistant_extras(message)
            if message.get("success") and not message.get("clarifying"):
                _render_research_actions(message["content"], f"msg_{idx}")

# ── Handle pending follow-up (submitted via button click) ────────────────────

if st.session_state.get("pending_followup"):
    followup_q = st.session_state.pop("pending_followup")
    st.session_state["suggested_followups"] = []
    st.session_state["research_directions"] = []
    st.session_state["messages"].append({"role": "user", "content": followup_q})
    _ensure_chat_id()
    _persist_current_chat()
    with st.chat_message("user"):
        st.markdown(followup_q)
    with st.chat_message("assistant"):
        with st.spinner(WAIT_MESSAGE):
            _run_query(platform_url, followup_q)
    st.rerun()

# ── Research direction buttons (pre-research scoping) ─────────────────────────

directions = st.session_state.get("research_directions", [])
if directions and st.session_state.get("awaiting_input"):
    st.markdown("---")
    st.markdown("**Select a research direction** *(click to proceed)*")
    for idx, direction in enumerate(directions):
        label = direction if len(direction) <= 90 else direction[:87] + "…"
        if st.button(label, key=f"dir_{idx}", use_container_width=True):
            st.session_state["pending_followup"] = direction
            st.session_state["research_directions"] = []
            st.rerun()
    st.markdown("---")

# ── Suggested follow-up question buttons ─────────────────────────────────────

followups = st.session_state.get("suggested_followups", [])
if followups and not st.session_state.get("awaiting_input"):
    st.markdown("---")
    st.markdown("**Suggested follow-up questions** *(click to research)*")
    for idx, question in enumerate(followups):
        label = question if len(question) <= 90 else question[:87] + "…"
        if st.button(label, key=f"fq_{idx}", use_container_width=True):
            st.session_state["pending_followup"] = question
            st.rerun()
    st.markdown("---")

# ── Chat input ────────────────────────────────────────────────────────────────

placeholder = (
    "Or type your own direction / add details…"
    if st.session_state.get("awaiting_input")
    else "Ask a legal research question…"
)

if prompt := st.chat_input(placeholder):
    st.session_state["suggested_followups"] = []
    st.session_state["research_directions"] = []
    st.session_state["messages"].append({"role": "user", "content": prompt})
    _ensure_chat_id()
    _persist_current_chat()
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner(WAIT_MESSAGE):
            _run_query(platform_url, prompt)
    st.rerun()
