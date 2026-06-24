# Review Agent — Production-Grade Audit & Fix Plan

> **Date:** 2026-06-23  
> **Scope:** Full codebase audit of `review_agent`, `document_core`, and Podman/VectorDB/MCP infrastructure  
> **Project Score: 62/100** (solid prototype, NOT production-ready)

---

## Table of Contents

1. [Executive Summary & Scoring](#1-executive-summary--scoring)
2. [CRITICAL — Podman / VectorDB / MCP Infrastructure](#2-critical--podman--vectordb--mcp-infrastructure)
3. [CRITICAL — Metadata Extraction at Sync Time](#3-critical--metadata-extraction-at-sync-time)
4. [CRITICAL — Policy Retrieval Production-Grade Fixes](#4-critical--policy-retrieval-production-grade-fixes)
5. [HIGH — HTTP Client & Connection Management](#5-high--http-client--connection-management)
6. [HIGH — LLM Gateway & Rate Limiting](#6-high--llm-gateway--rate-limiting)
7. [HIGH — Error Handling & Resilience](#7-high--error-handling--resilience)
8. [HIGH — State Management & Data Integrity](#8-high--state-management--data-integrity)
9. [MEDIUM — Observability & Monitoring](#9-medium--observability--monitoring)
10. [MEDIUM — Testing & Quality Assurance](#10-medium--testing--quality-assurance)
11. [MEDIUM — Configuration & Security](#11-medium--configuration--security)
12. [LOW — Code Quality & Design Issues](#12-low--code-quality--design-issues)
13. [LOW — Performance Optimization](#13-low--performance-optimization)
14. [Production Readiness Checklist](#14-production-readiness-checklist)

---

## 1. Executive Summary & Scoring

### Scoring Breakdown

| Dimension | Score | Max | Notes |
|---|---|---|---|
| Architecture & Design | 14 | 20 | Good LangGraph pipeline, but linear-only (no parallelism), tight coupling |
| Metadata & Data Integrity | 8 | 15 | Metadata extraction at sync is BROKEN — categories, policy_type lost on catalog fetch |
| Policy Retrieval Quality | 10 | 15 | Good multi-path retrieval, but no score calibration, no diversity, no staleness TTL |
| Error Handling & Resilience | 7 | 15 | Broad except clauses everywhere, no circuit breakers, no dead-letter queue |
| Observability & Monitoring | 5 | 10 | Metadata-in-state only, no structured logs, no metrics, no tracing |
| Infrastructure (Podman/DB/MCP) | 6 | 10 | No health-check container lifecycle, no connection pooling, synchronous SQLAlchemy in async pipeline |
| Security & Configuration | 5 | 10 | API keys in env without rotation, no mTLS, no input sanitization |
| Testing | 7 | 5 | Excellent test count (42 files!), but no integration/load tests against real VectorDB |
| **TOTAL** | **62** | **100** | |

### Key Verdict

The codebase has a **well-designed LangGraph pipeline** with impressive depth (section classification, multi-retrieval, guard pass, grounding, etc.), but suffers from **infrastructure-level problems** that will cause failures in production with Podman-deployed VectorDB:

1. **Metadata is silently lost** during policy sync — categories never reach the vector store
2. **HTTP client creates a new connection per request** instead of using persistent connections
3. **Synchronous SQLAlchemy** in an async pipeline blocks the event loop
4. **No circuit breakers** — a single slow MCP or LLM call stalls the entire pipeline
5. **Global mutable state** (`_limiter`, `_classify_parse_failures`, `lru_cache` settings) is unsafe in concurrent/multi-worker deployments

---

## 2. CRITICAL — Podman / VectorDB / MCP Infrastructure

### Problem 2.1: Synchronous SQLAlchemy Blocks Async Event Loop

**Files:** [`pgvector_store.py`](file:///d:/Ankit_legal/Legal/document_core/document_core/store/pgvector_store.py)

**Root Cause:** `PgVectorDocumentStore` uses `sqlalchemy.create_engine()` (synchronous) and `with self._engine.connect()` blocks. Every DB query blocks the Python event loop when called from `async` graph nodes via the Document MCP server. Under load with Podman, this causes:
- Event loop starvation
- Request timeouts cascading into retry storms
- Podman container hitting memory/CPU limits because threads pile up

**Evidence:** Lines 76, 84, 141, 252 — all use synchronous `conn.execute(text(...))`.

**Production-Grade Solution:**
```python
# Replace:
from sqlalchemy import create_engine
self._engine = create_engine(database_url, pool_pre_ping=True, future=True)

# With:
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
self._engine = create_async_engine(
    database_url.replace("postgresql://", "postgresql+asyncpg://"),
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=300,
)

# Then use:
async with self._engine.begin() as conn:
    result = await conn.execute(text(...))
```

**Impact:** Every DB call in the pipeline currently blocks. This is the #1 reason for Podman container instability.

---

### Problem 2.2: No Connection Pooling in HTTP Client

**Files:** [`document_client.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/clients/document_client.py#L42-L58)

**Root Cause:** The `_post()` method (line 50) creates a **new `httpx.AsyncClient` for every single request** when no injected client is provided:

```python
async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
    response = await client.post(url, json=payload)
```

This means every POST to document-mcp:
- Opens a new TCP connection
- Does TLS handshake (if applicable)
- Creates new HTTP/2 or HTTP/1.1 stream
- Closes connection after response

With ~20+ sections × 3 retrieval paths × 3 retry attempts = **180+ new connections** per review.

**Production-Grade Solution:**
```python
class DocumentMCPClient:
    def __init__(self, base_url, *, timeout_seconds=60.0, ...):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
                keepalive_expiry=30,
            ),
        )
    
    async def close(self):
        await self._client.aclose()
    
    async def _post(self, path, payload):
        # Use persistent client
        response = await self._client.post(path, json=payload)
        response.raise_for_status()
        return response.json()
```

Same issue exists in:
- [`health()`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/clients/document_client.py#L60-L67) — line 64
- [`get_section()`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/clients/document_client.py#L132-L152) — line 140
- [`get_policy_by_ref()`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/clients/document_client.py#L180-L208) — line 193
- [`get_contract_by_ref()`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/clients/document_client.py#L210-L238) — line 223
- [`HttpPolicyCatalogClient.fetch_policy()`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/clients/policy_catalog.py#L59-L71) — line 62

---

### Problem 2.3: No Container Health-Check Lifecycle

**Root Cause:** The `check_document_mcp()` preflight only runs once at graph start. If the Podman VectorDB container restarts mid-review (common with OOM kills), there's no:
- Automatic reconnection logic
- Health-check polling
- Graceful degradation

**Production-Grade Solution:**
```python
# Add to DocumentMCPClient:
async def _post_with_health_recovery(self, path, payload):
    for attempt in range(self.max_retries + 1):
        try:
            return await self._post(path, payload)
        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            if attempt < self.max_retries:
                await self._wait_for_healthy()
                continue
            raise

async def _wait_for_healthy(self, max_wait=30):
    """Block until MCP is healthy or timeout."""
    for _ in range(max_wait):
        try:
            health = await self.health()
            if health.get("status") == "ok":
                return
        except Exception:
            pass
        await asyncio.sleep(1)
    raise RuntimeError("document-mcp not recovered")
```

---

### Problem 2.4: Podman pgvector Container — Missing Configuration

**Root Cause:** No evidence of Podman-specific DB tuning. With pgvector in Podman:
- Default `shared_buffers` (128MB) is far too small for vector index
- No `maintenance_work_mem` tuning for HNSW/IVFFlat index builds
- No WAL configuration for crash recovery
- No resource limits defined

**Production-Grade Solution:**
```yaml
# podman-compose.yml or equivalent
services:
  vectordb:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    command: >
      postgres
        -c shared_buffers=512MB
        -c maintenance_work_mem=256MB
        -c effective_cache_size=1GB
        -c work_mem=16MB
        -c max_connections=100
        -c wal_level=replica
        -c max_wal_size=1GB
        -c checkpoint_completion_target=0.9
        -c random_page_cost=1.1
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: "2"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U legalai"]
      interval: 10s
      timeout: 5s
      retries: 5
```

---

## 3. CRITICAL — Metadata Extraction at Sync Time

### Problem 3.1: Categories Lost During Policy Catalog Sync

**Files:** [`policy_catalog.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/clients/policy_catalog.py#L90-L121)

**Root Cause:** In `index_fetched_policy()` (line 100), categories are extracted from `document.metadata.get("categories")`, but the `HttpPolicyCatalogClient.fetch_policy()` (line 59-71) does a simple `PolicyDocument.model_validate({**data, "ref": policy_ref})` without any metadata normalization or enrichment.

The problem chain:
1. Java catalog API returns policy with `categories` in varying formats: `["Liability Cap"]`, `["limitation_of_liability"]`, or even `null`
2. `PolicyDocument.metadata` stores whatever JSON the API returns — no validation
3. `normalize_categories()` is called in `index_fetched_policy()` but only on `document.metadata.get("categories")` — if the Java API puts categories at top-level instead of inside `metadata`, they're **silently lost**
4. The `IngestRequest.categories` validator normalizes, but **empty input normalizes to `[]`** — no warning

**Evidence:** The `PolicyDocument` model (line 22-31) has no `categories` field at the top level — it only has `metadata: dict`. So if the catalog API returns `{"ref": "p1", "title": "...", "categories": ["liability"]}`, those categories land in `metadata.categories` only if the API nests them there.

**Production-Grade Solution:**
```python
class PolicyDocument(BaseModel):
    ref: str
    title: str
    text: str
    policy_type: str | None = None
    applies_to_contract_types: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)  # ADD THIS
    document_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def merge_categories(self) -> Self:
        """Merge categories from metadata and top-level field."""
        meta_cats = self.metadata.get("categories", [])
        if isinstance(meta_cats, list):
            all_cats = list(self.categories) + meta_cats
        else:
            all_cats = list(self.categories)
        self.categories = normalize_categories(all_cats)
        return self

# Then in index_fetched_policy():
categories = normalize_categories(document.categories or list(document.metadata.get("categories") or []))
if not categories:
    logger.warning(
        "Policy '%s' (ref=%s) has no categories — metadata retrieval will be degraded",
        document.title, policy_ref,
    )
```

---

### Problem 3.2: No Metadata Extraction from Policy Text at Ingest Time

**Files:** [`pgvector_store.py`](file:///d:/Ankit_legal/Legal/document_core/document_core/store/pgvector_store.py#L88-L249) — `save_document()`

**Root Cause:** When a policy is ingested (synced from catalog or uploaded inline), the system stores whatever metadata was provided — but **never extracts metadata from the policy text itself**. This means:
- A policy titled "Limitation of Liability" with no `categories` field → stored with `categories: []`
- A policy with body text containing "indemnification obligations" → no auto-tagging
- Result: `list_document_ids_by_categories()` returns empty → retrieval falls back to unfiltered search → precision drops dramatically

**Production-Grade Solution:** Add auto-tagging at ingest time using the existing lexical classifier:

```python
# In document_core indexer or ingest pipeline:
from review_agent.services.section_category_lexical import _scan_text
from document_core.schemas.taxonomy import normalize_categories

def extract_metadata_at_ingest(
    title: str,
    sections: list[SectionNode],
    provided_categories: list[str],
) -> dict[str, Any]:
    """Auto-extract categories from policy text if not provided."""
    if provided_categories:
        return {"categories": normalize_categories(provided_categories)}
    
    # Extract from title
    from_title = _scan_text(title, title_priority=True)
    
    # Extract from first 3 section texts
    from_body = []
    for section in sections[:3]:
        from_body.extend(_scan_text(section.text[:800], title_priority=False))
    
    auto_categories = normalize_categories(list(set(from_title + from_body)))
    if auto_categories:
        return {"categories": auto_categories, "auto_tagged": True}
    
    return {"categories": ["general"], "auto_tagged": True}
```

---

### Problem 3.3: `applies_to_contract_types` Not Populated During Sync

**Files:** [`policy_catalog.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/clients/policy_catalog.py#L22-L31)

**Root Cause:** `PolicyDocument.applies_to_contract_types` defaults to `[]`. If the Java catalog doesn't explicitly set this field, the policy will never be filtered by `contract_type` during discovery, leading to either:
- All policies matching all contracts (noise) — if filter is disabled
- No policies matching typed contracts (silence) — if filter is enabled

**Production-Grade Solution:** Infer from policy content or require it at registration:

```python
class RegisterPolicyRequest(BaseModel):
    # ... existing fields ...
    applies_to_contract_types: list[str] = Field(
        default_factory=list,
        description="Required for contract-type filtering. Use ['*'] for universal policies.",
    )

    @model_validator(mode="after")
    def warn_empty_applies_to(self) -> Self:
        if not self.applies_to_contract_types:
            logger.warning(
                "Policy '%s' has empty applies_to_contract_types — "
                "will match ALL contracts or NONE depending on filter mode",
                self.title,
            )
        return self
```

---

### Problem 3.4: Content Hash Does Not Include Metadata

**Files:** [`pgvector_store.py`](file:///d:/Ankit_legal/Legal/document_core/document_core/store/pgvector_store.py#L28-L29)

**Root Cause:** `_content_hash()` only hashes `canonical_text`. So if you update a policy's categories, policy_type, or applies_to_contract_types without changing the text, the re-index is **silently skipped** (line 119: `if existing == content_hash: return`).

**Production-Grade Solution:**
```python
def _content_hash(canonical_text: str, metadata: dict | None = None) -> str:
    hasher = hashlib.sha256()
    hasher.update(canonical_text.encode("utf-8"))
    if metadata:
        # Sort keys for deterministic hash
        hasher.update(json.dumps(metadata, sort_keys=True).encode("utf-8"))
    return hasher.hexdigest()
```

---

## 4. CRITICAL — Policy Retrieval Production-Grade Fixes

### Problem 4.1: No Score Calibration Across Retrieval Paths

**Files:** [`multi_retrieval.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/services/multi_retrieval.py#L23-L40)

**Root Cause:** `_union_hits()` merges results from dense, FTS, and metadata paths using raw scores. But:
- Dense (cosine similarity): scores in [0, 1]
- FTS (ts_rank): scores in [0, ~0.6] typically
- Metadata path: artificially boosted by `discovery_category_score_boost=0.15`

The `max(existing.score, hit.score)` comparison at line 32 means FTS hits almost always lose to dense hits, even when FTS found a more relevant result.

**Production-Grade Solution:** Normalize scores before union:
```python
def _normalize_scores(hits: list[RetrievalHit], path: str) -> list[RetrievalHit]:
    if not hits:
        return hits
    max_score = max(h.score for h in hits)
    min_score = min(h.score for h in hits)
    range_val = max_score - min_score or 1.0
    return [
        h.model_copy(update={"score": (h.score - min_score) / range_val})
        for h in hits
    ]
```

---

### Problem 4.2: No Result Diversity — Same Policy Dominates

**Files:** [`multi_retrieval.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/services/multi_retrieval.py)

**Root Cause:** Retrieval returns top-K by score only. If one large policy has 50 sections, it can dominate all 10 return slots, hiding relevant sections from other policies.

**Production-Grade Solution:** Add MMR (Maximal Marginal Relevance) or simple document-level diversity:
```python
def _diverse_top_k(hits: list[RetrievalHit], top_k: int) -> list[RetrievalHit]:
    """Select top-k with at most 3 hits per document."""
    doc_count: dict[str, int] = {}
    selected = []
    MAX_PER_DOC = 3
    for hit in sorted(hits, key=lambda h: h.score, reverse=True):
        doc_id = str(hit.parent_chunk.document_id)
        if doc_count.get(doc_id, 0) >= MAX_PER_DOC:
            continue
        doc_count[doc_id] = doc_count.get(doc_id, 0) + 1
        selected.append(hit)
        if len(selected) >= top_k:
            break
    return selected
```

---

### Problem 4.3: No Staleness Check — Outdated Policies Not Invalidated

**Root Cause:** Once a policy is indexed, it stays `index_status='indexed'` forever. There's no:
- Content freshness TTL
- Periodic re-validation against catalog
- Version tracking

If the Java catalog updates a policy but the content hash hasn't changed (e.g., metadata-only update), the stale version persists.

**Production-Grade Solution:**
```sql
-- Add columns to policy_documents:
ALTER TABLE policy_documents ADD COLUMN catalog_version varchar(64);
ALTER TABLE policy_documents ADD COLUMN last_verified_at timestamptz DEFAULT now();
ALTER TABLE policy_documents ADD COLUMN expires_at timestamptz;

-- Periodic sweep:
UPDATE policy_documents SET index_status = 'stale'
WHERE last_verified_at < now() - interval '24 hours'
  AND index_status = 'indexed';
```

---

### Problem 4.4: Discovery Score Boosting is Arbitrary

**Files:** [`policy_discovery.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/services/policy_discovery.py#L409)

**Root Cause:** Line 409: `score = hit.score + settings.discovery_category_score_boost` adds a flat `0.15` boost to category-sweep results. This is:
- Not calibrated to actual score distributions
- Additive instead of multiplicative (can push a 0.05 score to 0.20, above `min_score=0.08`)
- Not normalized across topic and category searches

**Production-Grade Solution:** Use multiplicative boost with a ceiling:
```python
# Instead of: score = hit.score + boost
score = min(hit.score * (1.0 + settings.discovery_category_score_boost), 1.0)
```

---

## 5. HIGH — HTTP Client & Connection Management

### Problem 5.1: get_section() and get_policy_by_ref() Bypass Retry Logic

**Files:** [`document_client.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/clients/document_client.py#L132-L152)

**Root Cause:** `get_section()` (line 132) and `get_policy_by_ref()` (line 180) have their own inline HTTP code that **bypasses** `_post()` and its retry logic. If these endpoints fail transiently, there's no retry.

**Production-Grade Solution:** Refactor all methods to use `_post()` with 404 handling:
```python
async def get_section(self, request: GetSectionRequest) -> IndexedChunk | None:
    try:
        data = await self._post("/tools/get_section", request.model_dump(mode="json"))
        return IndexedChunk.model_validate(data)
    except RuntimeError as exc:
        if "404" in str(exc):
            return None
        raise
```

---

### Problem 5.2: No Request Timeout Differentiation

**Root Cause:** All requests use the same `timeout_seconds=60.0`. But:
- `health()` should timeout in 5s
- `search_policy()` could legitimately take 30s with large indices
- `ingest_document()` with re-parsing could take 120s

**Production-Grade Solution:**
```python
TIMEOUT_HEALTH = 5.0
TIMEOUT_SEARCH = 30.0
TIMEOUT_INGEST = 120.0
TIMEOUT_DEFAULT = 60.0

async def _post(self, path: str, payload: dict, *, timeout: float | None = None):
    effective_timeout = timeout or self.timeout_seconds
    # ...
```

---

## 6. HIGH — LLM Gateway & Rate Limiting

### Problem 6.1: Global Semaphore Not Shared Across Workers

**Files:** [`llm_gateway.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/models/llm_gateway.py#L24-L59)

**Root Cause:** `_limiter` is a module-level singleton. In multi-worker deployments (gunicorn/uvicorn workers), each worker gets its own `_limiter`. If `llm_global_concurrency=2` and you have 4 workers, the effective concurrency is 8 — likely exceeding the LLM provider's rate limit.

**Production-Grade Solution:** Use Redis-backed distributed semaphore:
```python
import aioredis

class DistributedLLMLimiter:
    def __init__(self, redis_url: str, max_concurrency: int):
        self._redis = aioredis.from_url(redis_url)
        self._max = max_concurrency
        self._key = "llm:semaphore"
    
    async def acquire(self):
        while True:
            count = await self._redis.incr(self._key)
            if count <= self._max:
                return
            await self._redis.decr(self._key)
            await asyncio.sleep(0.5)
    
    async def release(self):
        await self._redis.decr(self._key)
```

---

### Problem 6.2: Rate Limit Detection is Fragile String Matching

**Files:** [`llm_gateway.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/models/llm_gateway.py#L62-L89)

**Root Cause:** `_is_rate_limit_error()` relies on string pattern matching (`"429" in text`, `"rate limit" in text`). This:
- May false-positive on error messages containing "429" in other contexts
- Will miss rate limits from providers that use different status codes (e.g., Anthropic uses 529)
- Depth-limited to 4 causes — deeply wrapped exceptions are missed

**Production-Grade Solution:**
```python
_RATE_LIMIT_STATUS_CODES = {429, 529, 503}  # 503 = overloaded
_RATE_LIMIT_PATTERNS = re.compile(
    r"rate.?limit|too.?many.?requests|quota.?exceeded|throttl",
    re.IGNORECASE,
)

def _is_rate_limit_error(exc: BaseException) -> bool:
    current = exc
    for _ in range(6):  # Increase depth
        if current is None:
            break
        if hasattr(current, "status_code"):
            if current.status_code in _RATE_LIMIT_STATUS_CODES:
                return True
        if hasattr(current, "response"):
            code = getattr(current.response, "status_code", None)
            if code in _RATE_LIMIT_STATUS_CODES:
                return True
        if _RATE_LIMIT_PATTERNS.search(str(current)):
            return True
        current = current.__cause__ or current.__context__
    return False
```

---

### Problem 6.3: `invoke_structured` Holds Semaphore During Retries

**Files:** [`llm_gateway.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/models/llm_gateway.py#L199-L249)

**Root Cause:** The entire retry loop (up to 4 attempts with 30s sleep) runs **inside** `async with limiter.semaphore`. If `llm_global_concurrency=2` and both slots hit rate limits, the entire pipeline stalls for up to 120s (4 × 30s).

**Production-Grade Solution:** Release semaphore during backoff:
```python
async def invoke_structured(model, schema, *, system, user):
    cfg = get_settings()
    limiter = _get_limiter()
    max_attempts = max(0, cfg.llm_rate_limit_max_retries) + 1
    
    for attempt in range(max_attempts):
        async with limiter.semaphore:
            try:
                return await _invoke_once(model, schema, system=system, user=user)
            except Exception as exc:
                if not _is_rate_limit_error(exc):
                    raise
                last_exc = exc
                limiter.rate_limit_events += 1
        
        # Semaphore released — sleep outside
        delay = min(cfg.llm_rate_limit_backoff_base_seconds * (2**attempt),
                    cfg.llm_rate_limit_backoff_max_seconds)
        await asyncio.sleep(delay + random.uniform(0, 0.5))
    
    raise last_exc
```

---

## 7. HIGH — Error Handling & Resilience

### Problem 7.1: Broad `except Exception` Silently Swallows Errors

**Files:** Multiple — 23 instances across the codebase

**Root Cause:** Pattern `except Exception as exc: # noqa: BLE001` is used everywhere, often logging a warning and continuing. This masks:
- Configuration errors (wrong model name → unclear "structured output failed")
- Network partition (DNS failure → "batch classify failed: <connection error>")
- Data corruption (invalid UUID → "discovered policy has invalid document_id")

**Key Locations:**
| File | Line | Impact |
|---|---|---|
| `contract_routing.py` | L101, L114, L266 | Routing failure silenced → default 4 topics used |
| `section_classifier.py` | L344, L354, L380 | Classification failure → all sections become "general" |
| `section_compare_llm.py` | L329, L347 | Compare failure → sections marked INSUFFICIENT_POLICY_CONTEXT |
| `policy_catalog.py` | L69 | Catalog fetch failure → policy silently skipped |
| `memory_nodes.py` | L31, L82 | Memory failures silently ignored |

**Production-Grade Solution:** Differentiate recoverable vs. fatal errors:
```python
class RecoverableError(Exception):
    """Error that can be retried or degraded gracefully."""

class FatalPipelineError(Exception):
    """Error that should abort the pipeline."""

# In each handler:
try:
    result = await operation()
except httpx.ConnectError as exc:
    raise FatalPipelineError(f"MCP unreachable: {exc}") from exc
except httpx.HTTPStatusError as exc:
    if exc.response.status_code >= 500:
        raise RecoverableError(f"MCP server error: {exc}") from exc
    raise
except json.JSONDecodeError as exc:
    raise RecoverableError(f"Invalid JSON from LLM: {exc}") from exc
```

---

### Problem 7.2: No Circuit Breaker Pattern

**Root Cause:** If the LLM endpoint is down, every section classification, compare, guard pass, and routing call will:
1. Try structured output → fail
2. Fallback to raw JSON parse → fail  
3. Retry 1-3 times with backoff → fail
4. Return fallback result

For a 20-section contract, this means **~60 failed LLM calls** before the pipeline completes with garbage results.

**Production-Grade Solution:**
```python
class CircuitBreaker:
    def __init__(self, failure_threshold=5, reset_timeout=60):
        self._failures = 0
        self._threshold = failure_threshold
        self._reset_at: float | None = None
        self._state = "closed"  # closed, open, half_open
    
    def record_failure(self):
        self._failures += 1
        if self._failures >= self._threshold:
            self._state = "open"
            self._reset_at = time.time() + self._reset_timeout
    
    def record_success(self):
        self._failures = 0
        self._state = "closed"
    
    def allow_request(self) -> bool:
        if self._state == "closed":
            return True
        if self._state == "open" and time.time() > self._reset_at:
            self._state = "half_open"
            return True
        return self._state == "half_open"
```

---

### Problem 7.3: No Dead-Letter Queue for Failed Sections

**Root Cause:** When a section fails classification or comparison, it gets a fallback result and the pipeline continues. But there's no way to:
- Re-process only failed sections
- Track which sections consistently fail
- Alert on degraded review quality

**Production-Grade Solution:** Add a `failed_sections` field to `ReviewState`:
```python
class ReviewState(TypedDict, total=False):
    # ... existing fields ...
    failed_sections: list[dict[str, Any]]  # [{section_id, stage, error, retry_count}]
```

---

## 8. HIGH — State Management & Data Integrity

### Problem 8.1: `lru_cache` on `get_settings()` Persists Across Requests

**Files:** [`config.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/config.py#L195-L199)

**Root Cause:** `@lru_cache` on `get_settings()` means:
1. First request loads settings from `.env`
2. `.env` is modified (e.g., `LLM_MODEL` changed)
3. All subsequent requests use stale settings

`run_review()` calls `get_settings.cache_clear()` at line 101, but only for the review entry point — other code paths (e.g., background health checks, concurrent reviews) see stale config.

**Production-Grade Solution:** Time-based cache expiry:
```python
_settings_cache: ReviewSettings | None = None
_settings_cached_at: float = 0
_SETTINGS_TTL = 30.0  # seconds

def get_settings() -> ReviewSettings:
    global _settings_cache, _settings_cached_at
    now = time.monotonic()
    if _settings_cache is not None and (now - _settings_cached_at) < _SETTINGS_TTL:
        return _settings_cache
    _settings_cache = ReviewSettings()
    _settings_cached_at = now
    _maybe_warn_discovery_cap(_settings_cache)
    return _settings_cache
```

---

### Problem 8.2: Mutable Global `_classify_parse_failures` Counter

**Files:** [`section_classifier.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/services/section_classifier.py#L60)

**Root Cause:** `_classify_parse_failures` is a module-level counter incremented on parse errors. In production:
- It never resets between reviews
- It accumulates across concurrent reviews
- It degrades batch size permanently: `if _classify_parse_failures > 0: batch_size = 1`

**Production-Grade Solution:** Make it per-review via state:
```python
# Pass through settings or state instead of global:
async def classify_all_sections(
    sections: list[IndexedChunk],
    *,
    parse_failure_count: int = 0,  # From state
    ...
):
    batch_size = max(1, cfg.section_classify_batch_size)
    if parse_failure_count > 0:
        batch_size = max(1, min(batch_size, cfg.section_classify_batch_size_on_parse_fail))
```

---

### Problem 8.3: `MemorySaver` Checkpointer Unsuitable for Production

**Files:** [`review_graph.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/graph/review_graph.py#L33)

**Root Cause:** `_checkpointer = MemorySaver()` is a module-level in-memory checkpointer. It:
- Leaks memory for every review (checkpoints never garbage-collected)
- Lost on process restart
- Shared across all reviews in the same process

**Production-Grade Solution:**
```python
# Use PostgreSQL checkpointer:
from langgraph.checkpoint.postgres import PostgresSaver

_checkpointer = PostgresSaver.from_conn_string(database_url)
```

---

### Problem 8.4: ReviewState `warnings` Uses `operator.add` Reducer — Duplicates Accumulate

**Files:** [`review_state.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/state/review_state.py#L53)

**Root Cause:** `warnings: Annotated[list[str], operator.add]` means every node's warnings are **appended** to the list. But nodes like `discovery_nodes.py` (line 107) and `section_retrieval_nodes.py` (line 175) both return `"warnings"` keys — these accumulate, potentially creating hundreds of identical warnings.

**Production-Grade Solution:** Use a deduplicating reducer:
```python
def dedupe_append(existing: list[str], new: list[str]) -> list[str]:
    seen = set(existing)
    return existing + [w for w in new if w not in seen]

warnings: Annotated[list[str], dedupe_append]
```

---

## 9. MEDIUM — Observability & Monitoring

### Problem 9.1: No Structured Logging

**Root Cause:** All logging uses `logger.warning("message %s", value)`. There's no:
- JSON-structured log format for log aggregation (ELK/Datadog)
- Correlation IDs (tenant_id, thread_id, section_id)
- Log levels properly differentiated (WARNING used for informational messages)

**Production-Grade Solution:**
```python
import structlog

logger = structlog.get_logger()

# Usage:
logger.info(
    "policy_discovery_complete",
    tenant_id=tenant_id,
    thread_id=thread_id,
    discovered_count=len(capped),
    topics_searched=len(search_topics),
    contract_type_relaxed=contract_type_relaxed,
)
```

---

### Problem 9.2: No Metrics Collection

**Root Cause:** No Prometheus metrics, no StatsD, no custom counters. Can't track:
- Review latency (p50/p95/p99)
- LLM call success rate
- Retrieval hit rate per category
- Rate limit frequency

**Production-Grade Solution:**
```python
from prometheus_client import Counter, Histogram

REVIEW_DURATION = Histogram("review_duration_seconds", "Review pipeline duration")
LLM_CALLS = Counter("llm_calls_total", "LLM API calls", ["model", "status"])
RETRIEVAL_HITS = Histogram("retrieval_hits", "Policy hits per section", ["path"])
RATE_LIMITS = Counter("rate_limit_events_total", "LLM rate limit events")
```

---

### Problem 9.3: No Distributed Tracing

**Root Cause:** The pipeline has 12 sequential nodes, each with sub-calls (classify, retrieve, compare). Without tracing, debugging a slow review requires manual log correlation.

**Production-Grade Solution:** Add OpenTelemetry spans:
```python
from opentelemetry import trace

tracer = trace.get_tracer("review_agent")

async def section_policy_retrieval_node(state, client):
    with tracer.start_as_current_span("section_policy_retrieval") as span:
        span.set_attribute("section_count", len(sections))
        # ... existing code ...
```

---

## 10. MEDIUM — Testing & Quality Assurance

### Problem 10.1: No Integration Tests Against Real VectorDB

**Root Cause:** All 42 test files use mocked clients. There are no tests that:
- Connect to a real Podman pgvector instance
- Test embedding + retrieval roundtrip
- Verify category filtering SQL actually works
- Test concurrent access patterns

**Production-Grade Solution:**
```python
@pytest.fixture(scope="session")
def pgvector_db():
    """Spin up Podman pgvector for integration tests."""
    engine = create_engine("postgresql://test:test@localhost:5435/test_legalai")
    # Run migrations
    yield engine
    engine.dispose()

@pytest.mark.integration
async def test_category_filter_roundtrip(pgvector_db):
    store = PgVectorDocumentStore(str(pgvector_db.url))
    # Ingest policy with categories=["liability"]
    # Search with categories=["liability"]
    # Assert results found
    # Search with categories=["privacy"]
    # Assert no results
```

---

### Problem 10.2: No Load/Stress Tests

**Root Cause:** No evidence of tests simulating:
- 20+ sections × 40+ policies
- Concurrent reviews
- LLM timeout scenarios
- Database connection exhaustion

---

### Problem 10.3: Test Fixtures Don't Cover Edge Cases

**Root Cause:** Test fixtures (e.g., `conftest.py`) don't cover:
- Policies with empty categories
- Sections with Unicode/special characters
- Very large sections (>32K chars, which triggers truncation)
- Policies in multiple languages

---

## 11. MEDIUM — Configuration & Security

### Problem 11.1: API Keys in Plain Text .env

**Files:** [`.env.example`](file:///d:/Ankit_legal/Legal/review/review_agent/.env.example#L127)

**Root Cause:** `LLM_API_KEY=` is stored in `.env` files. In production:
- `.env` files can be committed to git
- No key rotation mechanism
- No audit trail for key usage

**Production-Grade Solution:**
- Use secret manager (Vault, AWS Secrets Manager)
- Mount secrets as Podman secrets: `podman secret create llm_api_key ./key.txt`
- Environment variable: `LLM_API_KEY_FILE=/run/secrets/llm_api_key`

---

### Problem 11.2: No Input Sanitization on Contract Text

**Root Cause:** `contract_text` is passed directly to LLM prompts without sanitization. Prompt injection attacks could:
- Override system instructions
- Extract policy content to external endpoints
- Manipulate compliance verdicts

**Production-Grade Solution:**
```python
def sanitize_contract_text(text: str) -> str:
    """Remove prompt injection patterns."""
    # Strip common injection patterns
    patterns = [
        r"ignore.*previous.*instructions",
        r"system:.*",
        r"<\|.*\|>",
    ]
    sanitized = text
    for pattern in patterns:
        sanitized = re.sub(pattern, "[REDACTED]", sanitized, flags=re.IGNORECASE)
    return sanitized
```

---

### Problem 11.3: `_injected_client` Exposes Private Field Access

**Files:** [`review_preflight.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/services/review_preflight.py#L61)

**Root Cause:** Line 61: `if client._injected_client is not None:` accesses a private field directly. This is a code smell and breaks encapsulation.

**Production-Grade Solution:** Add a method to `DocumentMCPClient`:
```python
async def raw_post(self, url: str, payload: dict) -> httpx.Response:
    """Direct POST for preflight probes."""
    if self._injected_client is not None:
        return await self._injected_client.post(url, json=payload)
    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
        return await client.post(url, json=payload)
```

---

## 12. LOW — Code Quality & Design Issues

### Problem 12.1: Duplicate Code in get_section / get_policy_by_ref / get_contract_by_ref

**Files:** [`document_client.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/clients/document_client.py#L132-L238)

**Root Cause:** `get_section()`, `get_policy_by_ref()`, and `get_contract_by_ref()` have nearly identical code (check injected client, make POST, handle 404). 106 lines that should be 20.

**Production-Grade Solution:** Extract common pattern:
```python
async def _post_nullable(self, path: str, payload: dict) -> dict | None:
    """POST with 404 → None handling."""
    try:
        return await self._post(path, payload)
    except RuntimeError as exc:
        if "404" in str(exc):
            return None
        raise
```

---

### Problem 12.2: `register_policy` and `register_contract` Have Untyped Returns

**Files:** [`document_client.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/clients/document_client.py#L162-L178)

**Root Cause:** `async def register_policy(self, request) -> Any:` — the `request` parameter and return type are `Any`, bypassing type checking. The inline import is also a code smell.

---

### Problem 12.3: `_config_cap_warned` Global Flag is Process-Sticky

**Files:** [`config.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/config.py#L16)

**Root Cause:** `_config_cap_warned = False` means the warning fires only once per process lifetime, not per review. In a long-running server, this is misleading.

---

### Problem 12.4: Prompt Templates Loaded from Disk on Every Call

**Files:** Multiple — `_load_prompt_template()` in `contract_routing.py`, `section_classifier.py`, `section_compare_llm.py`, `final_verify_llm.py`, `guard_pass.py`

**Root Cause:** Each call to these services re-reads the `.md` prompt file from disk. With 20 sections × batches, this means ~40+ file reads per review.

**Production-Grade Solution:** Cache at module level:
```python
@lru_cache(maxsize=1)
def _load_prompt_template() -> tuple[str, str]:
    # ... existing code ...
```

---

## 13. LOW — Performance Optimization

### Problem 13.1: Linear Pipeline — No Parallelism Between Independent Nodes

**Files:** [`review_graph.py`](file:///d:/Ankit_legal/Legal/review/review_agent/review_agent/graph/review_graph.py#L66-L79)

**Root Cause:** The graph is strictly linear: `load_memory → parser → clause → routing → discovery → ...`. But `load_memory` and `contract_parser` are independent — they could run in parallel.

**Production-Grade Solution:** Use LangGraph conditional branching or parallel fan-out:
```python
graph.add_edge(START, "load_memory")
graph.add_edge(START, "contract_parser")  # Parallel
graph.add_edge(["load_memory", "contract_parser"], "clause_detection")  # Join
```

---

### Problem 13.2: Embedding Generated Synchronously

**Files:** [`pgvector_store.py`](file:///d:/Ankit_legal/Legal/document_core/document_core/store/pgvector_store.py#L138-L139)

**Root Cause:** `embed_documents(child_texts)` is a synchronous call that generates embeddings for all chunks in series. For a 50-section policy, this blocks for several seconds.

**Production-Grade Solution:** Batch embeddings with async:
```python
# Use async embedding service with batching:
embeddings = await embed_documents_async(child_texts, batch_size=32)
```

---

### Problem 13.3: `save_document` Uses Nested Transactions

**Files:** [`pgvector_store.py`](file:///d:/Ankit_legal/Legal/document_core/document_core/store/pgvector_store.py#L109-L136)

**Root Cause:** Lines 109-136 and 141-249: Two separate `with self._engine.begin()` blocks for the same document. If the first succeeds and the second fails, the document is in an inconsistent state (registry says "indexed" but no chunks exist).

**Production-Grade Solution:** Single transaction:
```python
with self._engine.begin() as conn:
    # Check hash
    # Delete old chunks
    # Insert registry
    # Insert canonical text
    # Insert all chunks
    # All or nothing
```

---

## 14. Production Readiness Checklist

| # | Item | Status | Priority |
|---|---|---|---|
| 1 | Switch SQLAlchemy to async (asyncpg) | ❌ Not Done | P0 |
| 2 | Persistent HTTP connection pool in DocumentMCPClient | ❌ Not Done | P0 |
| 3 | Fix metadata extraction (categories at sync) | ❌ Not Done | P0 |
| 4 | Auto-tag policies with categories at ingest time | ❌ Not Done | P0 |
| 5 | Content hash includes metadata | ❌ Not Done | P0 |
| 6 | Release semaphore during LLM backoff | ❌ Not Done | P1 |
| 7 | Circuit breaker for LLM and MCP calls | ❌ Not Done | P1 |
| 8 | Replace MemorySaver with PostgresSaver | ❌ Not Done | P1 |
| 9 | Remove global mutable state (_classify_parse_failures) | ❌ Not Done | P1 |
| 10 | Structured logging (JSON format) | ❌ Not Done | P1 |
| 11 | Score normalization across retrieval paths | ❌ Not Done | P1 |
| 12 | Retrieval result diversity enforcement | ❌ Not Done | P2 |
| 13 | Podman resource limits and DB tuning | ❌ Not Done | P1 |
| 14 | Input sanitization (prompt injection) | ❌ Not Done | P1 |
| 15 | Secret management (not .env files) | ❌ Not Done | P2 |
| 16 | Integration tests against real VectorDB | ❌ Not Done | P2 |
| 17 | Prometheus metrics | ❌ Not Done | P2 |
| 18 | OpenTelemetry tracing | ❌ Not Done | P3 |
| 19 | Load tests | ❌ Not Done | P2 |
| 20 | Deduplicate warnings reducer | ❌ Not Done | P3 |
| 21 | Parallel graph nodes (memory + parser) | ❌ Not Done | P3 |
| 22 | Time-based settings cache expiry | ❌ Not Done | P2 |
| 23 | Policy staleness TTL | ❌ Not Done | P2 |
| 24 | Distributed rate limiter (Redis) | ❌ Not Done | P3 |
| 25 | Prompt template caching | ❌ Not Done | P3 |

---

> **Bottom Line:** The project demonstrates strong domain understanding and a sophisticated pipeline design. However, it is **firmly in the prototype stage** for infrastructure. The top 5 fixes (async DB, connection pooling, metadata extraction, semaphore release, circuit breakers) would move the score from 62→78. Adding observability and integration tests would push it to 85+.
