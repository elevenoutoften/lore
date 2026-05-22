from __future__ import annotations

from lore_app.schemas import EpistemicStatus, ExtractedClaim, ExtractionResult


def rpc(client, method, params=None, request_id=1):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        headers={"Mcp-Method": method},
    )


def test_epistemic_enum_values():
    assert [status.value for status in EpistemicStatus] == [
        "operator_declared",
        "retrieved",
        "inferred",
        "assumption",
        "hearsay",
    ]


def test_capture_with_epistemic_status(client):
    response = client.post(
        "/api/capture",
        json={
            "title": "Retrieved capture",
            "observation": "This came from a document.",
            "capture_date": "2026-05-22",
            "epistemic_status": "retrieved",
        },
    )

    assert response.status_code == 201, response.text
    page = response.json()["page"]
    assert page["epistemic_status"] == "retrieved"
    assert page["frontmatter"]["epistemic_status"] == "retrieved"

    reread = client.get(f"/api/pages/{page['id']}")
    assert reread.status_code == 200
    assert reread.json()["epistemic_status"] == "retrieved"


def test_capture_without_epistemic_status_backward_compat(client):
    response = client.post(
        "/api/capture",
        json={
            "title": "Legacy capture",
            "observation": "This should still work.",
            "capture_date": "2026-05-22",
        },
    )

    assert response.status_code == 201, response.text
    page = response.json()["page"]
    assert page["epistemic_status"] is None
    assert "epistemic_status" not in page["frontmatter"]


def test_page_frontmatter_with_epistemic_status(client):
    response = client.put(
        "/api/pages/services/epistemic-test",
        json={
            "content": "---\n"
            "title: Epistemic Test\n"
            "kind: service\n"
            "visibility: internal\n"
            "summary: Service with epistemic label.\n"
            "epistemic_status: operator_declared\n"
            "---\n\n"
            "# Epistemic Test\n"
        },
    )

    assert response.status_code == 200, response.text
    page = response.json()
    assert page["epistemic_status"] == "operator_declared"
    assert page["frontmatter"]["epistemic_status"] == "operator_declared"

    listed = client.get("/api/pages", params={"kind": "service"})
    assert listed.status_code == 200
    match = next(item for item in listed.json() if item["id"] == "services/epistemic-test")
    assert match["epistemic_status"] == "operator_declared"


def test_mcp_capture_with_epistemic_status(client):
    response = rpc(
        client,
        "tools/call",
        {
            "name": "lore_capture",
            "arguments": {
                "title": "Assumed capture",
                "observation": "This is an assumption.",
                "capture_date": "2026-05-22",
                "epistemic_status": "assumption",
            },
        },
    )

    assert response.status_code == 200, response.text
    page = response.json()["result"]["structuredContent"]["page"]
    assert page["epistemic_status"] == "assumption"
    assert page["frontmatter"]["epistemic_status"] == "assumption"


def test_excluded_claim_epistemic_propagation(client):
    ledger = client.app.state.ledger_db
    result = ExtractionResult(
        batch_id="batch-epistemic",
        claims=[
            ExtractedClaim(
                subject="services/lore",
                predicate="states",
                object="Lore records epistemic provenance.",
                confidence="high",
                epistemic_status=EpistemicStatus.inferred,
                source_page_ids=["inbox/2026-05-22/retrieved-capture"],
            )
        ],
        source_capture_ids=["inbox/2026-05-22/retrieved-capture"],
        processed_at="2026-05-22T00:00:00+00:00",
    )

    ledger.store_extraction_result(result)
    candidate = ledger.get_candidates(candidate_type="claim", limit=1)[0]

    assert candidate["epistemic_status"] == "inferred"
    assert candidate["content_json"]["epistemic_status"] == "inferred"
