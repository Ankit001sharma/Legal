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
    update_chat_title,
)

DEFAULT_PLATFORM_URL = "http://localhost:8080"
TIMEOUT_SECONDS = int(os.environ.get("STREAMLIT_QUERY_TIMEOUT", "900"))
WAIT_MESSAGE = "Researching… this can take a few minutes."

# ── Confidence badge config ────────────────────────────────────────────────────
_CONFIDENCE_BADGES: dict[str, tuple[str, str, str]] = {
    # key: (icon, label, CSS style)
    "ESTABLISHED": (
        "🟢",
        "Established law",
        "color:#155724;background:#d4edda;border:1px solid #c3e6cb",
    ),
    "LIKELY": (
        "🟡",
        "Likely — verify independently",
        "color:#856404;background:#fff3cd;border:1px solid #ffeeba",
    ),
    "UNCERTAIN": (
        "🔴",
        "Unsettled — limited precedent found",
        "color:#721c24;background:#f8d7da;border:1px solid #f5c6cb",
    ),
    "NOT_FOUND": (
        "⚫",
        "Not found in retrieved sources",
        "color:#383d41;background:#e2e3e5;border:1px solid #d6d8db",
    ),
}

RESEARCH_MODES = {
    "Normal Research": "normal",
    "Deep Research": "deep",
}
RESEARCH_MODE_DESCRIPTIONS = {
    "Normal Research": "Fast legal answer · 2-3 retrieval rounds · ~400-800 word response",
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
    headers: dict[str, str] | None = None,
) -> tuple[int | None, Any, str | None]:
    url = f"{base_url.rstrip('/')}{path}"
    request_headers = dict(headers or {})
    try:
        with httpx.Client(timeout=timeout) as client:
            if method == "GET":
                response = client.get(url, headers=request_headers)
            else:
                response = client.post(url, json=json_body, headers=request_headers)
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
    st.session_state.setdefault("session_id", None)
    st.session_state.setdefault("active_chat_id", None)
    st.session_state.setdefault("awaiting_input", False)
    st.session_state.setdefault("suggested_followups", [])
    st.session_state.setdefault("pending_followup", None)
    st.session_state.setdefault("research_directions", [])
    st.session_state.setdefault("research_mode", "Normal Research")
    st.session_state.setdefault("pending_chat_id", None)
    st.session_state.setdefault("pending_chat_delete", None)
    st.session_state.setdefault("last_user_query", None)       # for Regenerate
    st.session_state.setdefault("pending_regenerate", False)   # Regenerate trigger
    st.session_state.setdefault("ai_titled_chats", set())      # chats that have AI title
    st.session_state.setdefault("access_token", None)
    st.session_state.setdefault("user_id", None)
    st.session_state.setdefault("tenant_id", None)
    st.session_state.setdefault("user_email", None)
    st.session_state.setdefault("user_role", None)


def _auth_headers() -> dict[str, str]:
    token = st.session_state.get("access_token")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _history_scope() -> tuple[str | None, str]:
    return st.session_state.get("tenant_id"), st.session_state.get("user_id") or "_unknown"


def _render_login(platform_url: str) -> None:
    st.title("Legal AI Research — Sign in")
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        status, payload, error = _api(
            platform_url,
            "POST",
            "/auth/login",
            json_body={"email": email, "password": password},
            timeout=30,
        )
        if error:
            st.error(error)
            return
        if status != 200 or not isinstance(payload, dict):
            detail = payload.get("detail") if isinstance(payload, dict) else payload
            st.error(detail or "Login failed")
            return
        token = payload.get("access_token")
        if not token:
            st.error("Login response missing access_token")
            return
        me_status, me_payload, me_error = _api(
            platform_url,
            "GET",
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if me_error or me_status != 200 or not isinstance(me_payload, dict):
            st.error("Login succeeded but could not load profile")
            return
        st.session_state["access_token"] = token
        st.session_state["user_id"] = me_payload.get("user_id")
        st.session_state["tenant_id"] = me_payload.get("tenant_id")
        st.session_state["user_email"] = me_payload.get("email")
        st.session_state["user_role"] = me_payload.get("role")
        st.rerun()


def _ensure_session_id() -> str:
    """Return the frontend-owned session id sent to the platform API."""
    from_query = st.query_params.get("session_id")
    if from_query:
        st.session_state["session_id"] = from_query
        st.session_state["active_chat_id"] = from_query
        return from_query

    session_id = st.session_state.get("session_id")
    if session_id:
        return session_id

    chat_id = st.session_state.get("active_chat_id")
    if chat_id:
        st.session_state["session_id"] = chat_id
        return chat_id

    chat_id = new_chat_id()
    st.session_state["active_chat_id"] = chat_id
    st.session_state["session_id"] = chat_id
    return chat_id


def _ensure_chat_id() -> str:
    _ensure_session_id()
    return st.session_state["active_chat_id"]


def _persist_current_chat() -> None:
    messages = st.session_state.get("messages") or []
    if not messages:
        return
    chat_id = _ensure_chat_id()
    tenant_id, user_id = _history_scope()
    existing = load_chat(chat_id, tenant_id=tenant_id, user_id=user_id)
    save_chat(
        chat_id=chat_id,
        messages=messages,
        thread_id=st.session_state.get("session_id"),
        research_mode=st.session_state.get("research_mode", "Normal Research"),
        awaiting_input=bool(st.session_state.get("awaiting_input")),
        created_at=existing.get("created_at") if existing else None,
        tenant_id=tenant_id,
        user_id=user_id,
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
    tenant_id, user_id = _history_scope()
    data = load_chat(chat_id, tenant_id=tenant_id, user_id=user_id)
    if not data:
        return

    st.session_state["active_chat_id"] = chat_id
    st.session_state["messages"] = data.get("messages") or []
    st.session_state["session_id"] = data.get("thread_id") or chat_id
    st.session_state["research_mode"] = data.get("research_mode", "Normal Research")
    st.session_state["research_mode_radio"] = st.session_state["research_mode"]
    st.session_state["awaiting_input"] = bool(data.get("awaiting_input"))
    st.session_state["research_directions"] = []
    st.session_state["pending_followup"] = None

    # Restore last user query for Regenerate
    for m in reversed(data.get("messages") or []):
        if m.get("role") == "user":
            st.session_state["last_user_query"] = m.get("content")
            break

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
    st.session_state["session_id"] = None
    st.session_state["active_chat_id"] = None
    st.session_state["awaiting_input"] = False
    st.session_state["suggested_followups"] = []
    st.session_state["pending_followup"] = None
    st.session_state["research_directions"] = []
    st.session_state["last_user_query"] = None
    st.session_state["pending_regenerate"] = False


# ── Output sanitization ───────────────────────────────────────────────────────

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
    cleaned = text.replace("⚠️", "").replace("✅", "")
    cleaned = _EMOJI_RE.sub("", cleaned)
    lines = []
    for line in cleaned.splitlines():
        lines.append(re.sub(r"[ \t]+", " ", line).rstrip())
    return "\n".join(lines)


# ── Follow-up question parsing ────────────────────────────────────────────────

def _parse_followup_questions(text: str) -> list[str]:
    questions: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"#+\s*suggested follow.up", stripped, re.IGNORECASE):
            in_section = True
            continue
        if in_section and re.match(r"^#{1,3}\s", stripped) and not re.match(
            r"#+\s*suggested follow.up", stripped, re.IGNORECASE
        ):
            break
        if in_section:
            m = re.match(r"^(\d+)[.)]\s+(.+)", stripped)
            if m:
                question = m.group(2).strip()
                if len(question) > 15:
                    questions.append(question)
    return questions[:5]


# ── SSE streaming query ───────────────────────────────────────────────────────

def _stream_query(platform_url: str, prompt: str) -> dict[str, Any] | None:
    """Try SSE streaming. Returns the ``done`` payload dict, or None on failure.

    While streaming, updates two Streamlit placeholders in place:
    - A status line showing which node is running.
    - A content area that grows word-by-word.

    Returns None when the /query/stream endpoint is unavailable — the caller
    should fall back to the regular /query endpoint.
    """
    selected_label = st.session_state.get("research_mode", "Normal Research")
    mode_value = RESEARCH_MODES.get(selected_label, "normal")
    body: dict[str, Any] = {
        "query": prompt,
        "task_type": ["research"],
        "max_results": 10,
        "mode": mode_value,
    }
    body["session_id"] = _ensure_session_id()

    url = f"{platform_url.rstrip('/')}/query/stream"

    status_ph = st.empty()
    content_ph = st.empty()
    full_content = ""
    streaming_started = False
    final_payload: dict[str, Any] | None = None

    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
            with client.stream(
                "POST", url, json=body, headers=_auth_headers()
            ) as response:
                if response.status_code != 200:
                    status_ph.empty()
                    content_ph.empty()
                    return None

                buffer = ""
                for raw_chunk in response.iter_text():
                    buffer += raw_chunk
                    # SSE messages are separated by double newline
                    while "\n\n" in buffer:
                        raw_msg, buffer = buffer.split("\n\n", 1)
                        data_parts = [
                            ln[6:] for ln in raw_msg.splitlines() if ln.startswith("data: ")
                        ]
                        if not data_parts:
                            continue
                        try:
                            event = json.loads(data_parts[-1])
                        except json.JSONDecodeError:
                            continue

                        evt_type = event.get("type", "")

                        if evt_type == "progress":
                            icon = event.get("icon", "⏳")
                            msg = event.get("message", "Working…")
                            status_ph.markdown(
                                f"<p style='color:#888;font-style:italic;margin:4px 0'>"
                                f"{icon} {msg}…</p>",
                                unsafe_allow_html=True,
                            )

                        elif evt_type == "stream_start":
                            streaming_started = True
                            status_ph.empty()

                        elif evt_type == "token" and streaming_started:
                            full_content += event.get("text", "")
                            content_ph.markdown(full_content + "▌")

                        elif evt_type == "stream_replace":
                            # Post-processing complete: swap in the final version
                            # (citations linkified to HTML, deterministic checks, etc.)
                            full_content = event.get("text", "")
                            content_ph.markdown(full_content, unsafe_allow_html=True)

                        elif evt_type == "done":
                            final_payload = event
                            if full_content:
                                content_ph.markdown(full_content, unsafe_allow_html=True)
                            status_ph.empty()
                            break

                        elif evt_type == "error":
                            status_ph.empty()
                            content_ph.error(f"Error: {event.get('message', 'Unknown error')}")
                            break

    except (httpx.ConnectError, httpx.RemoteProtocolError):
        # Stream endpoint not available — caller falls back to /query.
        status_ph.empty()
        content_ph.empty()
        return None
    except httpx.TimeoutException:
        status_ph.empty()
        content_ph.error(f"Request timed out after {TIMEOUT_SECONDS}s.")
        return None
    except Exception as exc:  # noqa: BLE001
        status_ph.empty()
        content_ph.error(f"Streaming error: {exc}")
        return None

    return final_payload


# ── Regular (non-streaming) query ─────────────────────────────────────────────

def _run_query(platform_url: str, prompt: str) -> None:
    selected_label = st.session_state.get("research_mode", "Normal Research")
    mode_value = RESEARCH_MODES.get(selected_label, "normal")
    body: dict[str, Any] = {
        "query": prompt,
        "task_type": ["research"],
        "max_results": 10,
        "mode": mode_value,
    }
    body["session_id"] = _ensure_session_id()

    status, payload, error = _api(
        platform_url, "POST", "/query", json_body=body, timeout=TIMEOUT_SECONDS,
        headers=_auth_headers(),
    )

    if error:
        return _push_assistant(f"Error: {error}", success=False)
    if status != 200 or not isinstance(payload, dict):
        return _push_assistant(f"Error: Unexpected response (HTTP {status}).", success=False)

    _process_query_payload(platform_url, prompt, payload)


def _process_query_payload(
    platform_url: str, prompt: str, payload: dict[str, Any]
) -> None:
    """Shared logic for processing a completed query payload (streaming or not)."""
    if payload.get("session_id"):
        st.session_state["session_id"] = payload["session_id"]
    st.session_state["awaiting_input"] = bool(payload.get("awaiting_input"))
    st.session_state["research_directions"] = payload.get("research_directions") or []

    output = (payload.get("output") or "").strip()
    meta = {
        k: v for k, v in payload.items()
        if k not in ("output", "type", "success")
    }
    # Ensure confidence_level is always present in meta for badge rendering
    meta.setdefault("confidence_level", payload.get("confidence_level", ""))

    if payload.get("awaiting_input"):
        text = output or "Could you share a bit more detail so I can refine the research?"
        _push_assistant(text, success=True, clarifying=True, meta=meta, platform_url=platform_url, prompt=prompt)
    elif payload.get("success", True):
        text = output or "The request completed but returned no text. Check the gateway logs."
        _push_assistant(text, success=True, meta=meta, platform_url=platform_url, prompt=prompt)
    else:
        text = payload.get("error") or "Research failed."
        _push_assistant(f"Error: {text}", success=False, meta=meta)


# ── Submit query (streaming with fallback) ────────────────────────────────────

def _submit_query(platform_url: str, prompt: str) -> None:
    """Try SSE streaming; fall back to regular /query if unavailable."""
    payload = _stream_query(platform_url, prompt)

    if payload is None:
        # Stream endpoint not available — use blocking /query with spinner.
        with st.spinner(WAIT_MESSAGE):
            _run_query(platform_url, prompt)
        return

    _process_query_payload(platform_url, prompt, payload)


# ── Push assistant message + auto-title ──────────────────────────────────────

def _auto_title(platform_url: str, query: str) -> None:
    """Async-fire: generate an LLM title and persist it. Best-effort."""
    chat_id = st.session_state.get("active_chat_id")
    if not chat_id:
        return
    if chat_id in st.session_state.get("ai_titled_chats", set()):
        return
    status, payload, _ = _api(
        platform_url, "POST", "/title", json_body={"query": query}, timeout=12,
        headers=_auth_headers(),
    )
    if status == 200 and isinstance(payload, dict):
        title = payload.get("title", "").strip()
        if title:
            tenant_id, user_id = _history_scope()
            update_chat_title(chat_id, title, tenant_id=tenant_id, user_id=user_id)
            st.session_state.setdefault("ai_titled_chats", set()).add(chat_id)


def _push_assistant(
    text: str,
    *,
    success: bool,
    clarifying: bool = False,
    meta: dict[str, Any] | None = None,
    platform_url: str | None = None,
    prompt: str | None = None,
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

        # Auto-title: trigger only on the very first successful research response.
        if platform_url and prompt:
            user_msgs = [m for m in st.session_state["messages"] if m["role"] == "user"]
            if len(user_msgs) == 1:
                _auto_title(platform_url, prompt)
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


def _render_confidence_badge(confidence_level: str) -> None:
    """Display a colour-coded confidence badge above the research answer."""
    cfg = _CONFIDENCE_BADGES.get(
        confidence_level.upper().replace(" ", "_"),
        _CONFIDENCE_BADGES["ESTABLISHED"],
    )
    icon, label, style = cfg
    st.markdown(
        f'<span style="display:inline-block;font-size:13px;padding:3px 10px;'
        f'border-radius:4px;font-weight:600;{style}">{icon} {label}</span>',
        unsafe_allow_html=True,
    )


def _render_icon_toolbar(
    text: str,
    *,
    action_key: str,
    download_filename: str,
) -> None:
    """Copy / Download icon row shown below research content."""
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


# ── PDF export ────────────────────────────────────────────────────────────────

def _render_pdf_export_button(content: str, key: str) -> None:
    """Inject a Print/PDF button that opens a formatted memo in a new window."""
    escaped = json.dumps(content.strip())
    components.html(
        f"""
        <style>
          .pdf-btn {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 5px 14px;
            background: #1a1a2e;
            color: white;
            border: none;
            border-radius: 6px;
            font-size: 13px;
            cursor: pointer;
            margin-top: 4px;
          }}
          .pdf-btn:hover {{ background: #16213e; }}
        </style>
        <button class="pdf-btn" onclick="openPrint_{key}()">🖨️ Export as PDF</button>
        <script>
        function openPrint_{key}() {{
          var md = {escaped};
          var html = md
            .replace(/^#### (.+)$/gm, '<h4>$1</h4>')
            .replace(/^### (.+)$/gm, '<h3>$1</h3>')
            .replace(/^## (.+)$/gm, '<h2>$1</h2>')
            .replace(/^# (.+)$/gm, '<h1>$1</h1>')
            .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')
            .replace(/\\*(.+?)\\*/g, '<em>$1</em>')
            .replace(/`(.+?)`/g, '<code>$1</code>')
            .replace(/\\n\\n/g, '</p><p>')
            .replace(/\\n/g, '<br>');
          var win = window.open('', '_blank');
          win.document.write(
            '<html><head><title>Legal Research Memo</title>' +
            '<style>' +
            'body{{font-family:Georgia,serif;max-width:820px;margin:40px auto;' +
            'line-height:1.7;color:#1a1a1a;padding:0 24px}}' +
            'h1{{color:#1a1a2e;border-bottom:2px solid #1a1a2e;padding-bottom:8px}}' +
            'h2{{color:#16213e;border-bottom:1px solid #ccc;padding-bottom:4px;margin-top:32px}}' +
            'h3{{color:#0f3460;margin-top:24px}}h4{{color:#333}}' +
            'table{{border-collapse:collapse;width:100%;margin:16px 0}}' +
            'th,td{{border:1px solid #ddd;padding:8px 12px;text-align:left}}' +
            'th{{background:#f5f5f5;font-weight:600}}' +
            'code{{background:#f5f5f5;padding:2px 5px;border-radius:3px;font-size:0.9em}}' +
            'blockquote{{border-left:4px solid #1a1a2e;margin:0;padding-left:16px;color:#555}}' +
            '@media print{{body{{margin:20px}}}}' +
            '</style></head><body><p>' + html + '</p></body></html>'
          );
          win.document.close();
          setTimeout(function(){{ win.print(); }}, 400);
        }}
        </script>
        """,
        height=42,
    )


# ── Copy buttons for code blocks (injected into parent page) ─────────────────

def _inject_copy_buttons_js() -> None:
    """Inject JS that adds a Copy button to every <pre> code block on the page."""
    components.html(
        """
        <script>
        (function() {
          function addCopyBtns() {
            var pres = window.parent.document.querySelectorAll(
              '.stMarkdown pre, .element-container pre'
            );
            pres.forEach(function(pre) {
              if (pre.querySelector('.cp-btn')) return;
              pre.style.position = 'relative';
              var btn = document.createElement('button');
              btn.className = 'cp-btn';
              btn.title = 'Copy code';
              btn.innerHTML = '&#x1F4CB;';
              btn.style.cssText = [
                'position:absolute', 'top:6px', 'right:8px',
                'border:none', 'background:rgba(0,0,0,.15)',
                'color:#eee', 'border-radius:5px',
                'padding:2px 7px', 'font-size:13px',
                'cursor:pointer', 'z-index:10'
              ].join(';');
              btn.addEventListener('click', function(e) {
                e.stopPropagation();
                var code = pre.querySelector('code');
                var text = code ? code.innerText : pre.innerText;
                window.parent.navigator.clipboard.writeText(text).then(function() {
                  btn.innerHTML = '&#x2713;';
                  setTimeout(function() { btn.innerHTML = '&#x1F4CB;'; }, 1600);
                });
              });
              pre.appendChild(btn);
            });
          }
          // Run once after DOM settles, then watch for new blocks.
          setTimeout(addCopyBtns, 800);
          var obs = new MutationObserver(function() { addCopyBtns(); });
          obs.observe(window.parent.document.body, { childList: true, subtree: true });
        })();
        </script>
        """,
        height=0,
    )


# ── Chat history sidebar ──────────────────────────────────────────────────────

def _render_chat_history_sidebar() -> None:
    tenant_id, user_id = _history_scope()
    chats = list_chats(tenant_id=tenant_id, user_id=user_id)
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
_inject_copy_buttons_js()  # Add copy buttons to all code blocks

_default_platform_url = os.environ.get("LEGAL_AI_PLATFORM_URL", DEFAULT_PLATFORM_URL)
if not st.session_state.get("access_token"):
    _render_login(_default_platform_url)
    st.stop()

platform_url = _default_platform_url

# Handle deferred chat switch/delete.
if st.session_state.get("pending_chat_delete"):
    delete_id = st.session_state.pop("pending_chat_delete")
    if delete_id == st.session_state.get("active_chat_id"):
        _reset_chat(persist=False)
    delete_chat(delete_id, tenant_id=_history_scope()[0], user_id=_history_scope()[1])
    st.rerun()

if st.session_state.get("pending_chat_id"):
    switch_id = st.session_state.pop("pending_chat_id")
    if switch_id != st.session_state.get("active_chat_id"):
        _persist_current_chat()
        _load_chat_into_state(switch_id)
    st.rerun()

with st.sidebar:
    st.header("⚖️ Legal AI Research")
    if st.session_state.get("user_email"):
        st.caption(f"Signed in as {st.session_state['user_email']}")
    if st.button("Sign out", key="sign_out_btn"):
        for key in ("access_token", "user_id", "tenant_id", "user_email", "user_role"):
            st.session_state[key] = None
        _reset_chat(persist=False)
        st.rerun()

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
    if st.session_state.get("session_id"):
        st.caption(f"Session: `{st.session_state['session_id']}`")
    st.caption(
        "Start the platform gateway with:\n\n"
        "```\nuvicorn legal_ai_platform.gateway.app:app --port 8080\n```"
    )

# ── Main area ─────────────────────────────────────────────────────────────────

st.title("Legal Research Assistant")

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

all_messages = st.session_state["messages"]
last_assistant_idx = max(
    (i for i, m in enumerate(all_messages) if m["role"] == "assistant"),
    default=None,
)

for idx, message in enumerate(all_messages):
    with st.chat_message(message["role"]):
        if message["role"] == "assistant" and message.get("success") and not message.get("clarifying"):
            confidence = (message.get("meta") or {}).get("confidence_level", "")
            if confidence:
                _render_confidence_badge(confidence)
            # Research answers may contain HTML anchor tags from linkify_citations
            st.markdown(_sanitize_output_text(message["content"]), unsafe_allow_html=True)
            _render_assistant_extras(message)
            _render_research_actions(message["content"], f"msg_{idx}")
            if idx == last_assistant_idx:
                _render_pdf_export_button(message["content"], key=f"pdf_{idx}")
        else:
            st.markdown(_sanitize_output_text(message["content"]))
            if message["role"] == "assistant":
                _render_assistant_extras(message)

# ── Regenerate button (shown below last assistant message) ────────────────────

if (
    last_assistant_idx is not None
    and all_messages[last_assistant_idx].get("success")
    and not all_messages[last_assistant_idx].get("clarifying")
    and st.session_state.get("last_user_query")
    and not st.session_state.get("awaiting_input")
):
    regen_col, _ = st.columns([1, 4])
    with regen_col:
        if st.button("🔄 Regenerate", key="regen_btn", help="Re-run the last query"):
            st.session_state["pending_regenerate"] = True
            st.rerun()

# ── Handle pending regenerate ─────────────────────────────────────────────────

if st.session_state.get("pending_regenerate"):
    st.session_state["pending_regenerate"] = False
    regen_prompt = st.session_state.get("last_user_query", "")
    if regen_prompt:
        # Remove the last assistant message so we don't show a duplicate
        msgs = st.session_state["messages"]
        while msgs and msgs[-1]["role"] == "assistant":
            msgs.pop()
        st.session_state["messages"] = msgs
        st.session_state["suggested_followups"] = []
        _persist_current_chat()
        with st.chat_message("user"):
            st.markdown(regen_prompt)
        with st.chat_message("assistant"):
            _submit_query(platform_url, regen_prompt)
        st.rerun()

# ── Handle pending follow-up (submitted via button click) ────────────────────

if st.session_state.get("pending_followup"):
    followup_q = st.session_state.pop("pending_followup")
    st.session_state["suggested_followups"] = []
    st.session_state["research_directions"] = []
    st.session_state["messages"].append({"role": "user", "content": followup_q})
    st.session_state["last_user_query"] = followup_q
    _ensure_chat_id()
    _persist_current_chat()
    with st.chat_message("user"):
        st.markdown(followup_q)
    with st.chat_message("assistant"):
        _submit_query(platform_url, followup_q)
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

# ── Suggested follow-up question chips ────────────────────────────────────────

followups = st.session_state.get("suggested_followups", [])
if followups and not st.session_state.get("awaiting_input"):
    st.markdown("---")
    st.markdown("**Suggested follow-up questions** *(click to research)*")
    # Render as a horizontal chip row using columns
    cols = st.columns(min(len(followups), 2))
    for idx, question in enumerate(followups):
        col = cols[idx % len(cols)]
        with col:
            label = question if len(question) <= 80 else question[:77] + "…"
            if st.button(
                label,
                key=f"fq_{idx}",
                use_container_width=True,
                help=question,
            ):
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
    st.session_state["last_user_query"] = prompt
    st.session_state["messages"].append({"role": "user", "content": prompt})
    _ensure_chat_id()
    _persist_current_chat()
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        _submit_query(platform_url, prompt)
    st.rerun()
