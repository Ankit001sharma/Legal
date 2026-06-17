import pytest
from httpx import ASGITransport, AsyncClient

from document_core.store.memory_store import InMemoryDocumentStore, set_store
from mcp.document_server.main import app
from review_agent.clients.document_client import DocumentMCPClient
from review_agent.graph.review_graph import run_review
from tests.fixtures import SAMPLE_CONTRACT, SAMPLE_POLICY


@pytest.fixture(autouse=True)
def isolated_store():
    set_store(InMemoryDocumentStore())


@pytest.mark.asyncio
async def test_document_server_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/health")
        assert response.status_code == 200
        assert response.json()["service"] == "document-mcp"


@pytest.mark.asyncio
async def test_review_graph_text_e2e():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        client = DocumentMCPClient("http://test", http_client=http)
        result = await run_review(
            client=client,
            tenant_id="demo",
            contract_text=SAMPLE_CONTRACT,
            contract_title="Vendor MSA",
            policy_texts=[{"title": "Vendor Policy", "text": SAMPLE_POLICY}],
            contract_type="msa",
        )
    report = result["report"]
    assert report is not None
    assert report.findings
    assert "Limitation of Liability" in report.summary_markdown
