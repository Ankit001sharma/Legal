"""Dev UI backend — serves static frontend + test API (Java sync + review)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
OUTPUTS = ROOT / "outputs"

sys.path.insert(0, str(ROOT))
from bootstrap_env import load_env, setup_pythonpath  # noqa: E402

load_env()
setup_pythonpath()

from java_sync_stub.sync_client import JavaSyncStub  # noqa: E402
from review_agent.clients.document_client import DocumentMCPClient  # noqa: E402
from review_agent.config import get_settings  # noqa: E402
from review_agent.graph.review_graph import run_review  # noqa: E402
from review_output import (  # noqa: E402
    build_platform_review_payload,
    build_review_output_envelope,
)

app = FastAPI(title="Legal Review Dev UI", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConfigBody(BaseModel):
    document_server_url: str = "http://localhost:8003"
    platform_url: str = "http://localhost:8080"
    tenant_id: str = "e2e-demo"


class ReviewBody(BaseModel):
    contract_document_id: str | None = None
    contract_title: str = "Mutual NDA (Dev UI)"
    contract_type: str = "nda"
    use_platform: bool = False


class TextSectionBody(BaseModel):
    section_id: str
    title: str = ""
    text: str = ""


class CustomPolicyBody(BaseModel):
    title: str
    policy_ref: str | None = None
    categories: str = "general"
    review_guidance: str = ""
    policy_type: str = "nda"
    section_title: str = "Policy Standard"
    text: str = ""


class CustomContractBody(BaseModel):
    title: str = "My Contract"
    contract_type: str = "nda"
    contract_ref: str | None = None
    sections: list[TextSectionBody] = Field(default_factory=list)


class CustomSyncBody(BaseModel):
    contract: CustomContractBody
    policies: list[CustomPolicyBody] = Field(default_factory=list)
    run_review: bool = True


def _slug_ref(prefix: str, title: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", (title or prefix).lower()).strip("-")[:40]
    return f"{prefix}-{slug or 'doc'}-{int(time.time())}"


def _build_custom_sync_payload(body: CustomSyncBody, tenant: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    contract_sections = [
        {"section_id": s.section_id, "title": s.title, "text": s.text.strip()}
        for s in body.contract.sections
        if (s.text or "").strip()
    ]
    if not contract_sections:
        raise HTTPException(400, detail="Add at least one contract section with text")

    contract_ref = body.contract.contract_ref or _slug_ref("contract", body.contract.title)
    contract_payload: dict[str, Any] = {
        "tenant_id": tenant,
        "contract_ref": contract_ref,
        "title": body.contract.title,
        "contract_type": body.contract.contract_type,
        "source": "dev-ui-custom",
        "metadata": {"source": "dev-ui-custom"},
        "sections": contract_sections,
    }

    policy_payloads: list[dict[str, Any]] = []
    for idx, policy in enumerate(body.policies, start=1):
        text = (policy.text or "").strip()
        if not text:
            continue
        cats = [c.strip() for c in policy.categories.split(",") if c.strip()]
        policy_ref = policy.policy_ref or _slug_ref(f"policy-{idx}", policy.title)
        policy_payloads.append(
            {
                "tenant_id": tenant,
                "policy_ref": policy_ref,
                "title": policy.title,
                "policy_type": policy.policy_type,
                "applies_to_contract_types": [body.contract.contract_type],
                "categories": cats or ["general"],
                "review_guidance": policy.review_guidance.strip(),
                "source": "dev-ui-custom",
                "metadata": {"source": "dev-ui-custom", "categories": cats or ["general"]},
                "sections": [
                    {
                        "section_id": "1",
                        "title": policy.section_title or policy.title,
                        "text": text,
                    }
                ],
            }
        )
    if not policy_payloads:
        raise HTTPException(400, detail="Add at least one policy with text")

    return contract_payload, policy_payloads


def _client(url: str | None = None) -> DocumentMCPClient:
    base = url or os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")
    return DocumentMCPClient(base)


def _stub(tenant: str | None = None, url: str | None = None) -> JavaSyncStub:
    tenant_id = tenant or os.environ.get("E2E_TENANT_ID", "e2e-demo")
    return JavaSyncStub(_client(url), tenant_id=tenant_id)


def _write_output(name: str, data: dict[str, Any]) -> Path:
    OUTPUTS.mkdir(exist_ok=True)
    path = OUTPUTS / name
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _port_listeners(port: int) -> list[int]:
    import re
    import subprocess

    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pids: set[int] = set()
    pattern = re.compile(rf":{port}\s+.*LISTENING\s+(\d+)\s*$")
    for line in result.stdout.splitlines():
        match = pattern.search(line.strip())
        if match:
            pids.add(int(match.group(1)))
    return sorted(pids)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    return {
        "document_server_url": os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003"),
        "platform_url": os.environ.get("PLATFORM_URL", "http://localhost:8080"),
        "tenant_id": os.environ.get("E2E_TENANT_ID", "e2e-demo"),
        "llm_configured": bool(os.environ.get("LLM_API_KEY") or os.environ.get("MISTRAL_API_KEY")),
    }


@app.post("/api/config")
async def save_config(body: ConfigBody) -> dict[str, str]:
    os.environ["DOCUMENT_SERVER_URL"] = body.document_server_url
    os.environ["PLATFORM_URL"] = body.platform_url
    os.environ["E2E_TENANT_ID"] = body.tenant_id
    return {"status": "ok"}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    result: dict[str, Any] = {"document_mcp": None, "platform": None}
    doc_url = os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")
    parsed_port = 8003
    if "://" in doc_url:
        try:
            parsed_port = int(doc_url.rsplit(":", 1)[-1].split("/", 1)[0])
        except ValueError:
            parsed_port = 8003
    listeners = _port_listeners(parsed_port)
    result["port_listeners"] = [{"port": parsed_port, "pid": pid} for pid in listeners]
    result["port_listener_count"] = len(listeners)
    try:
        mcp_health = await _client(doc_url).health()
        result["document_mcp"] = mcp_health
        result["mcp_capabilities"] = list(mcp_health.get("capabilities") or [])
        result["mcp_build_id"] = mcp_health.get("build_id") or ""
    except Exception as exc:  # noqa: BLE001
        result["document_mcp"] = {"status": "error", "detail": str(exc)}
        result["mcp_capabilities"] = []
        result["mcp_build_id"] = ""

    platform_url = os.environ.get("PLATFORM_URL", "http://localhost:8080")
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(f"{platform_url.rstrip('/')}/health")
            result["platform"] = r.json() if r.status_code == 200 else {"status": "error", "code": r.status_code}
    except Exception as exc:  # noqa: BLE001
        result["platform"] = {"status": "unreachable", "detail": str(exc)}

    return result


@app.post("/api/sync")
async def sync_all() -> dict[str, Any]:
    stub = _stub()
    health = await stub.health_ok()
    if health.get("db") != "ok":
        raise HTTPException(503, detail=f"document-mcp unhealthy: {health}")

    sync = await stub.sync_all_fixtures()
    verify = await stub.verify_contract_indexed(sync["contract"]["document_id"])
    sync["verify"] = verify
    _write_output("sync_result.json", sync)
    return sync


@app.post("/api/sync-custom")
async def sync_custom(body: CustomSyncBody) -> dict[str, Any]:
    stub = _stub()
    health = await stub.health_ok()
    if health.get("db") != "ok":
        raise HTTPException(503, detail=f"document-mcp unhealthy: {health}")

    tenant = os.environ.get("E2E_TENANT_ID", "e2e-demo")
    contract_payload, policy_payloads = _build_custom_sync_payload(body, tenant)
    sync = await stub.sync_custom(contract=contract_payload, policies=policy_payloads)
    _write_output("sync_result.json", sync)
    return sync


@app.post("/api/custom-review")
async def custom_review(body: CustomSyncBody) -> dict[str, Any]:
    """Sync pasted contract + policies, then run prod review."""
    body.run_review = True
    stub = _stub()
    health = await stub.health_ok()
    if health.get("db") != "ok":
        raise HTTPException(503, detail=f"document-mcp unhealthy: {health}")

    if not os.environ.get("LLM_API_KEY") and not os.environ.get("MISTRAL_API_KEY"):
        raise HTTPException(400, detail="Set LLM_API_KEY in temp_java_sync/.env")

    tenant = os.environ.get("E2E_TENANT_ID", "e2e-demo")
    contract_payload, policy_payloads = _build_custom_sync_payload(body, tenant)
    sync = await stub.sync_custom(contract=contract_payload, policies=policy_payloads)
    _write_output("sync_result.json", sync)

    contract_id = sync["contract"]["document_id"]
    os.environ.setdefault("REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID", "true")
    os.environ.setdefault("REVIEW_REJECT_INLINE_POLICIES", "true")
    get_settings.cache_clear()

    doc_url = os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")
    state = await run_review(
        client=_client(doc_url),
        tenant_id=tenant,
        contract_document_id=contract_id,
        contract_title=body.contract.title,
        contract_type=body.contract.contract_type,
    )
    report = state.get("report")
    if report is None:
        raise HTTPException(500, detail={"warnings": state.get("warnings"), "error": "no report", "sync": sync})

    payload = build_review_output_envelope(
        report=report,
        state=state,
        contract_document_id=contract_id,
    )
    payload["sync"] = sync
    _write_output("review_result.json", payload)
    return payload


@app.post("/api/review")
async def review(body: ReviewBody) -> dict[str, Any]:
    sync_path = OUTPUTS / "sync_result.json"
    contract_id = body.contract_document_id
    if not contract_id and sync_path.is_file():
        contract_id = json.loads(sync_path.read_text(encoding="utf-8"))["contract"]["document_id"]
    if not contract_id:
        raise HTTPException(400, detail="No contract_document_id — run sync first")

    tenant = os.environ.get("E2E_TENANT_ID", "e2e-demo")

    if body.use_platform:
        import httpx

        platform_url = os.environ.get("PLATFORM_URL", "http://localhost:8080")
        payload = build_platform_review_payload(
            tenant_id=tenant,
            contract_document_id=contract_id,
            contract_title=body.contract_title,
            contract_type=body.contract_type,
        )
        async with httpx.AsyncClient(timeout=600.0) as http:
            r = await http.post(f"{platform_url.rstrip('/')}/query", json=payload)
            if r.status_code >= 400:
                raise HTTPException(r.status_code, detail=r.text)
            data = r.json()
        _write_output("review_result.json", data)
        return data

    if not os.environ.get("LLM_API_KEY") and not os.environ.get("MISTRAL_API_KEY"):
        raise HTTPException(400, detail="Set LLM_API_KEY in temp_java_sync/.env")

    os.environ.setdefault("REVIEW_REQUIRE_CONTRACT_DOCUMENT_ID", "true")
    os.environ.setdefault("REVIEW_REJECT_INLINE_POLICIES", "true")
    get_settings.cache_clear()

    doc_url = os.environ.get("DOCUMENT_SERVER_URL", "http://localhost:8003")
    state = await run_review(
        client=_client(doc_url),
        tenant_id=tenant,
        contract_document_id=contract_id,
        contract_title=body.contract_title,
        contract_type=body.contract_type,
    )
    report = state.get("report")
    if report is None:
        raise HTTPException(500, detail={"warnings": state.get("warnings"), "error": "no report"})

    payload = build_review_output_envelope(
        report=report,
        state=state,
        contract_document_id=contract_id,
    )
    _write_output("review_result.json", payload)
    return payload


@app.post("/api/tombstone")
async def tombstone() -> dict[str, Any]:
    stub = _stub()
    result = await stub.tombstone_smoke("playbook-indemnification-standard")
    await stub.sync_policy_from_fixture(
        ROOT / "fixtures" / "policies" / "indemnification_standard.json"
    )
    result["restored"] = True
    _write_output("tombstone_result.json", result)
    return result


@app.post("/api/full-e2e")
async def full_e2e() -> dict[str, Any]:
    from run_full_e2e import main

    code = await main()
    log_path = OUTPUTS / "e2e_log.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.is_file() else {}
    if code != 0:
        raise HTTPException(500, detail=log)
    return log


@app.get("/api/outputs/{name}")
async def get_output(name: str) -> dict[str, Any]:
    allowed = {"sync_result.json", "review_result.json", "e2e_log.json", "tombstone_result.json"}
    if name not in allowed:
        raise HTTPException(404, detail="unknown output file")
    path = OUTPUTS / name
    if not path.is_file():
        raise HTTPException(404, detail="not found — run an action first")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    import uvicorn

    port = int(os.environ.get("DEV_UI_PORT", "8090"))
    print(f"Dev UI: http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
