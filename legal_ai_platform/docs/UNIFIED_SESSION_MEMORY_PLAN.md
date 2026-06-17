# Unified Session Memory — Implementation Plan

**Document ID:** `PLAN-UNIFIED-MEMORY-v1.0`  
**Status:** Planning  
**Scope:** **Memory & session only** — one chat panel, many agents, one shared memory  
**Out of scope:** Document index (pgvector), Celery queues, PDF ingest, HITL  

---

## 1. Problem (what is wrong today)

Users work in **one conversation** but memory is **split by agent**:

| Layer | Research today | Review today | User expectation |
|-------|----------------|--------------|------------------|
| Conversation transcript | `sessions/{thread_id}.jsonl` (research package) | **Not used** | One transcript for all turns |
| Long-term facts | retrieval-mcp `MEMORY.md` | Same MCP, but review saves **only report** | One memory pool for all agents |
| Session summary | `build_session_context()` (research) | **None** | Follow-ups work after review |
| LangGraph checkpoint | `MemorySaver` per graph (research) | Separate `MemorySaver` (review) | One session state or shared context |
| Matter artifacts | N/A | `artifacts.report` returned once, **not stored in session** | “Explain liability finding” without resending contract |

**Symptom:** User does research → review → follow-up question → **context is lost** or only research remembers.

**Root cause:** Memory is **agent-owned**, not **platform-owned**.

---

## 2. Design principle (locked)

```text
ONE thread_id  →  ONE platform session  →  ALL agents read/write the SAME stores
```

- **No** `research_memory` vs `review_memory`
- **No** separate MCP servers for memory per agent
- **Yes** orchestrator owns session lifecycle before/after every agent call
- **Yes** retrieval-mcp remains the **only** long-term memory MCP (`/tools/memory/*`)

---

## 3. Target architecture

```text
                    POST /query  (thread_id)
                           │
                    QueryOrchestrator
                           │
              ┌────────────┴────────────┐
              │   SessionService        │  ← NEW (platform-owned)
              │   - load_session()      │
              │   - append_turn()       │
              │   - get_matter_context()│
              └────────────┬────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   ResearchAgent     ReviewAgent      Future agents
         │                 │                 │
         └─────────────────┴─────────────────┘
                           │
              ┌────────────┴────────────┐
              │  Shared storage (by thread_id) │
              ├──────────────────────────────┤
              │ 1. Transcript (JSONL/DB)     │  all user + assistant turns
              │ 2. Session summary (rolling) │  compact older context
              │ 3. Matter snapshot (JSON)    │  contract, policies, last report
              │ 4. Long-term memory (MCP)    │  retrieval-mcp MEMORY.md
              └──────────────────────────────┘
```

---

## 4. Four memory layers (one session)

### Layer A — Conversation transcript (platform)

**Owner:** `legal_ai_platform.session`  
**Key:** `thread_id` (+ `tenant_id`)  
**Format:** Ordered turns:

```json
{
  "role": "user|assistant",
  "agent": "research|review|null",
  "task_type": "research|review",
  "content": "...",
  "timestamp": "...",
  "artifacts_ref": "optional pointer to matter snapshot version"
}
```

**Rules:**
- Orchestrator **appends user message** before routing
- Orchestrator **appends assistant message** after agent returns
- **Every agent** receives `session.transcript_recent` + `session.summary` in context

**Storage (phased):**
- Phase 1: File JSONL under `SESSION_DIR/{tenant_id}/{thread_id}.jsonl` (same layout as research, but **platform path**)
- Phase 2: Postgres `session_turns` table

---

### Layer B — Rolling session summary (platform)

**Owner:** `SessionService`  
**Purpose:** Long chats stay within token budget  

- After each turn (or every N turns), update `session.summary` (short paragraph)
- Research already has `build_session_context` — **move logic to platform**, research calls platform API instead of local files only

---

### Layer C — Matter snapshot (platform)

**Owner:** `SessionService`  
**Purpose:** Cross-agent handoff without resending full payloads  

```json
{
  "contract_text": "...",
  "contract_title": "...",
  "policies": [...],
  "last_review_report_id": "...",
  "last_review_report": { ... },
  "last_agent": "review",
  "last_task_type": "review"
}
```

**Rules:**
- Review agent **writes** snapshot after successful review
- Follow-up “explain liability” → orchestrator injects `last_review_report` into research/review context
- Review follow-up **may omit** `contract_text` if snapshot exists

---

### Layer D — Long-term memory (retrieval-mcp, shared)

**Owner:** retrieval-mcp `MemoryService` (unchanged)  
**Endpoints:** `/tools/memory/save`, `/tools/memory/search`  

**Rules:**
- **Platform** decides **when** to save (not each agent ad hoc)
- Save **durable facts** only (verified findings, key legal conclusions) — not full chat logs
- Tags in `hook` or content prefix: `[research]`, `[review]`, `tenant`, `thread_id`

**Deprecate:** Per-agent `save_memory` nodes calling MCP with different formats — replace with `SessionService.commit_long_term_memory()`.

---

## 5. Orchestrator flow (every `/query`)

```text
1. Resolve thread_id (client supplied or platform generates UUID)
2. session = SessionService.load(thread_id, tenant_id)
3. session.append_user_turn(query)
4. Enrich AgentRequest:
     - context.session_transcript = session.recent_turns(k=20)
     - context.session_summary = session.summary
     - context.matter = session.matter_snapshot
5. task_type = classifier.classify(query, task_type, enriched_context)
6. response = agent.execute(enriched_request)
7. session.append_assistant_turn(response)
8. session.update_matter_from_artifacts(response.artifacts)
9. session.maybe_summarize()  # rolling summary
10. session.maybe_persist_long_term_memory()  # MCP save rules
11. SessionService.save(session)
12. return response with thread_id
```

---

## 6. Agent contract changes (minimal)

All agents receive **same** `SessionContext` shape in `request.context`:

```python
class SessionContext(TypedDict):
    thread_id: str
    transcript_recent: list[Turn]
    summary: str
    matter: MatterSnapshot
    memory_snippets: str  # from MCP search, pre-fetched by platform
```

| Agent | Must do |
|-------|---------|
| **Research** | Stop owning transcript paths; use `context.session_*`; keep LangGraph messages for **this run only** |
| **Review** | Read `matter.contract_text` / `matter.policies` if request omits them; write `artifacts.report` → platform stores in matter |
| **Future** | Same contract |

**Remove from review agent (later):** standalone `load_memory` / `save_memory` graph nodes — platform preloads `memory_snippets` and post-saves durable facts.

---

## 7. Classifier / router (session-aware)

Phase 1: keyword + context rules  
Phase 2: LLM router with session

| Signal | Route |
|--------|-------|
| `matter.last_review_report` + “explain finding / liability / policy” | `research` or `review` with report in context |
| `contract_text` + `policies` in request or matter | `review` |
| Default | `research` |

**Explicit `task_type` always wins.**

---

## 8. Phased implementation

### Phase 0 — Foundation (Week 1)

| Task | Deliverable |
|------|-------------|
| Define `SessionContext`, `MatterSnapshot`, `Turn` models | `legal_ai_platform/models/session.py` |
| `SessionService` file backend | `session/service.py`, `session/file_store.py` |
| Unit tests | create thread, append turns, load matter |

**Exit:** Two fake agent calls share same transcript file.

---

### Phase 1 — Orchestrator integration (Week 2)

| Task | Deliverable |
|------|-------------|
| Wire `SessionService` in `QueryOrchestrator.handle()` | Before/after agent per §5 |
| Always return `thread_id` on `AgentResponse` | Gateway contract |
| `GET /sessions/{thread_id}` (debug) | Optional read API |

**Exit:** Three turns in one `thread_id` visible in one JSONL transcript (any agent).

---

### Phase 2 — Matter snapshot + review follow-up (Week 3)

| Task | Deliverable |
|------|-------------|
| Store `artifacts.report` in matter after review | No resend contract on follow-up |
| Relax review validation: policies from matter snapshot | Orchestrator `_validate_review_payload` |
| Review reads `context.matter` | `ReviewAgent.execute()` |

**Exit:** E2E: research → review → “explain liability finding” with same `thread_id`.

---

### Phase 3 — Unified long-term memory (Week 4)

| Task | Deliverable |
|------|-------------|
| `SessionService.search_long_term(query)` → retrieval MCP | One entry point |
| `SessionService.save_long_term(title, content, hook)` | Platform tags agent + thread |
| Remove duplicate save from review graph node | Platform post-turn hook only |
| Research: route MCP saves through platform when called from `/query` | Optional bridge |

**Exit:** Review report searchable in next session via MCP; hook shows `[review][tenant][thread]`.

---

### Phase 4 — Research alignment (Week 5)

| Task | Deliverable |
|------|-------------|
| Research `load_memory` uses platform transcript + summary | Deprecate duplicate jsonl paths OR symlink same `SESSION_DIR` |
| Single `DEEP_RESEARCH_MEMORY_DIR` + `PLATFORM_SESSION_DIR` documented | `.env.example` |
| Config: `SESSION_DIR` default shared | Docker compose |

**Exit:** Research multi-turn + review share **one** transcript directory per thread.

---

### Phase 5 — Durable storage (Week 6+, production)

| Task | Deliverable |
|------|-------------|
| Postgres `session_turns`, `matter_snapshots` | Migrations |
| LangGraph Postgres checkpointer (optional) | Shared or per-agent with same `thread_id` in metadata |
| Tenant isolation on all session queries | `tenant_id` required |

**Exit:** Restart platform → session survives.

---

## 9. Environment variables

| Variable | Purpose |
|----------|---------|
| `PLATFORM_SESSION_DIR` | Transcript + matter files (default: `memory/sessions`) |
| `DEEP_RESEARCH_MEMORY_DIR` | Long-term MCP memory root (must match retrieval-mcp) |
| `RETRIEVAL_SERVER_URL` | MCP memory search/save |
| `SESSION_SUMMARY_EVERY_N_TURNS` | Rolling summary trigger (default: 5) |
| `SESSION_TRANSCRIPT_MAX_TURNS` | Inject limit (default: 20) |

---

## 10. What we do NOT do (avoid wrong turns)

| Do not | Why |
|--------|-----|
| New `review-mcp` memory server | One retrieval-mcp is enough |
| Separate memory DB per agent | Violates unified session |
| Store full contract in MEMORY.md every turn | Use matter snapshot + document index later |
| Make review a chat LLM loop first | Session layer works with current deterministic graph |

---

## 11. Acceptance criteria (definition of done)

1. **One panel:** User sends 3+ messages with same `thread_id` → one transcript, mixed agents allowed.
2. **Research → review:** Second message triggers review without losing first research answer in context.
3. **Review → follow-up:** Third message references liability finding without resending contract/policies.
4. **Long-term:** Fourth message in **new** session finds prior review fact via MCP search (if saved).
5. **No agent-only memory paths** in production code paths (research file backend becomes adapter to platform store).

---

## 12. File layout (target)

```text
legal_ai_platform/
├── docs/
│   └── UNIFIED_SESSION_MEMORY_PLAN.md   ← this file
├── src/legal_ai_platform/
│   ├── session/
│   │   ├── __init__.py
│   │   ├── models.py          # Turn, MatterSnapshot, SessionState
│   │   ├── service.py         # SessionService
│   │   ├── file_store.py      # Phase 1 persistence
│   │   └── memory_bridge.py   # retrieval MCP long-term
│   ├── orchestration/
│   │   └── orchestrator.py    # load/append session each turn
│   └── models/
│       └── session.py         # SessionContext on AgentRequest
```

---

## 13. Sprint 1 starter tasks (when coding begins)

1. `SessionService` + file store + tests  
2. Orchestrator append user/assistant turns  
3. Matter snapshot after review  
4. One integration test: research stub → review stub → follow-up sees prior turns  

---

*End of plan. Memory is platform-owned; agents are stateless workers with shared session context.*
