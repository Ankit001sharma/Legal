# Phase 33 — Security (Review Path)

**Plan ID:** `DR-PHASE-33-SECURITY`  
**Priority:** P2  
**Duration:** ~3–4 days  
**Depends on:** None  
**Non-goals:** Java auth gateway, OAuth, mTLS, WAF, frontend CSP

---

## 1. Goal

Harden **Python review agent** inputs and secrets for production deploy — minimal changes, no new auth service.

---

## 2. Root causes

| # | Root cause | Risk |
|---|------------|------|
| R1 | `LLM_API_KEY` in plain `.env` | Key leak via git, logs, backups |
| R2 | `contract_text` passed raw to LLM prompts | Prompt injection → wrong compliance verdict |
| R3 | No max-length guard on inline contract in API | DoS via huge payload |

---

## 3. Task map

| # | Task | Est. | Files | Risk |
|---|------|------|-------|------|
| **T1** | Secret file loader | 4h | `config.py`, `.env.example` | Low |
| **T2** | Contract text sanitizer | 4h | `review_agent/security/sanitize.py` (NEW) | Low |
| **T3** | Wire sanitizer pre-LLM | 4h | routing, classifier, compare, guard | Low |
| **T4** | Payload size limits | 2h | platform `AgentRequest` / review entry | Low |
| **T5** | Tests | 4h | `tests/test_sanitize.py` | Low |

---

## 4. T1 — Secret from file (minimal)

```python
# config.py — in ReviewSettings validator or model_post_init
def _resolve_api_key() -> str | None:
    if path := os.getenv("LLM_API_KEY_FILE"):
        return Path(path).read_text(encoding="utf-8").strip()
    return os.getenv("LLM_API_KEY")
```

`.env.example`:
```env
# LLM_API_KEY=           # dev only
LLM_API_KEY_FILE=/run/secrets/llm_api_key  # prod (Podman secret mount)
```

**Do not** implement Vault SDK — file mount is enough for Podman/K8s.

---

## 5. T2 — Prompt injection sanitizer (minimal)

```python
# security/sanitize.py
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"<\|[^|]+\|>"),  # special tokens
    re.compile(r"^system\s*:", re.I | re.M),
]

def sanitize_for_llm(text: str, *, max_chars: int = 120_000) -> str:
    clipped = text[:max_chars]
    for pat in _INJECTION_PATTERNS:
        clipped = pat.sub("[REDACTED]", clipped)
    return clipped
```

**Conservative** — redact patterns, do not strip legal content.

---

## 6. T3 — Wire points (3 call sites only)

Apply `sanitize_for_llm` in:
1. `contract_routing.py` — contract summary input  
2. `section_compare_llm.py` — section + policy text  
3. `guard_pass.py` — final verify input  

**Not** every LLM call — those three carry user-controlled contract text.

---

## 7. T4 — Size limits

`REVIEW_MAX_CONTRACT_CHARS=500_000` (env, default 500k).

Reject at `run_review` entry with `FatalPipelineError` before graph build.

---

## 8. Definition of done

- [ ] Review runs with `LLM_API_KEY_FILE` and no `LLM_API_KEY`
- [ ] Injection fixture `"Ignore previous instructions"` → redacted in prompt (unit test)
- [ ] Oversize contract rejected with clear error code
- [ ] No Java gateway changes

---

## 9. Out of scope

- API authentication / JWT
- Tenant authorization
- PII redaction (separate compliance track)
- Secret rotation automation
