"""FastAPI application for the Document MCP server.

Exposes /tools/* HTTP endpoints (same pattern as retrieval-mcp) so platform
clients can call ingest, search, and grounding without importing server code.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from document_core.schemas.chunk import (
    DocumentKind,
    GetSectionRequest,
    GroundingCheckRequest,
    GroundingCheckResult,
    IndexedChunk,
    IngestRequest,
    IngestResult,
    ListSectionsRequest,
    RetrievalHit,
    SearchRequest,
)
from document_core.services.grounding import verify_quote
from document_core.services.ingest import ingest_document
from document_core.services.search import get_section, list_sections, search_contract, search_policy
from mcp.document_server.config import SERVICE_NAME, VERSION, get_settings

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class ListPoliciesRequest(BaseModel):
    tenant_id: str
    kind: DocumentKind = DocumentKind.POLICY


class ListPoliciesResponse(BaseModel):
    tenant_id: str
    document_ids: list[str]


class ToolResponse(BaseModel):
    request_id: str
    result: Any
    latency_ms: int


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    logger.info("service starting service=%s version=%s", SERVICE_NAME, VERSION)
    yield
    logger.info("shutting down service=%s", SERVICE_NAME)


app = FastAPI(
    title="Document MCP Server",
    description="Contract and policy document ingest, RAG search, and grounding",
    version=VERSION,
    lifespan=lifespan,
)


def _request_id() -> str:
    return uuid.uuid4().hex[:12]


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service=SERVICE_NAME, version=VERSION)


@app.post("/tools/ingest_document", response_model=IngestResult)
async def ingest_document_tool(request: IngestRequest) -> IngestResult:
    return await ingest_document(request)


@app.post("/tools/index_policy", response_model=IngestResult)
async def index_policy_tool(request: IngestRequest) -> IngestResult:
    payload = request.model_copy(update={"kind": DocumentKind.POLICY})
    return await ingest_document(payload)


@app.post("/tools/search_contract")
async def search_contract_tool(request: SearchRequest) -> dict[str, Any]:
    hits = await search_contract(request)
    return {"results": [h.model_dump(mode="json") for h in hits]}


@app.post("/tools/search_policy")
async def search_policy_tool(request: SearchRequest) -> dict[str, Any]:
    hits = await search_policy(request)
    return {"results": [h.model_dump(mode="json") for h in hits]}


@app.post("/tools/list_sections")
async def list_sections_tool(request: ListSectionsRequest) -> dict[str, Any]:
    sections = await list_sections(request)
    return {"sections": [s.model_dump(mode="json") for s in sections]}


@app.post("/tools/get_section")
async def get_section_tool(request: GetSectionRequest) -> IndexedChunk:
    section = await get_section(request)
    if section is None:
        raise HTTPException(status_code=404, detail="section not found")
    return section


@app.post("/tools/verify_quote", response_model=GroundingCheckResult)
async def verify_quote_tool(request: GroundingCheckRequest) -> GroundingCheckResult:
    return await verify_quote(request)


@app.post("/tools/verify_policy_quote", response_model=GroundingCheckResult)
async def verify_policy_quote_tool(request: GroundingCheckRequest) -> GroundingCheckResult:
    return await verify_quote(request)


@app.post("/tools/list_policies", response_model=ListPoliciesResponse)
async def list_policies_tool(request: ListPoliciesRequest) -> ListPoliciesResponse:
    from document_core.store.memory_store import get_store

    store = get_store()
    doc_ids = store.list_documents(request.tenant_id, DocumentKind.POLICY)
    return ListPoliciesResponse(
        tenant_id=request.tenant_id,
        document_ids=[str(doc_id) for doc_id in doc_ids],
    )


@app.middleware("http")
async def log_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    request_id = request.headers.get("x-request-id", _request_id())
    response = await call_next(request)
    latency_ms = int((time.perf_counter() - started) * 1000)
    response.headers["x-request-id"] = request_id
    if request.url.path.startswith("/tools/"):
        logger.info(
            "tool_call path=%s status=%s latency_ms=%s request_id=%s",
            request.url.path,
            response.status_code,
            latency_ms,
            request_id,
        )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error path=%s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": str(exc)})
