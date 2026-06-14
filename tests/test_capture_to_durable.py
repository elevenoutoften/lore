from __future__ import annotations

from fastapi.testclient import TestClient

from lore_app.config import LoreConfig
from lore_app.main import create_app


def make_client(tmp_path) -> TestClient:
    content_dir = tmp_path / "pages"
    content_dir.mkdir()
    config = LoreConfig()
    config.content_dir = content_dir
    config.search_db = tmp_path / "search.db"
    config.ledger_db = tmp_path / "ledger.db"
    config.vector_db = tmp_path / "vectors.db"
    config.api_keys_db = tmp_path / "api_keys.db"
    config.trusted_headers = True
    config.auto_consolidate = False  # this test drives the manual pipeline explicitly
    return TestClient(create_app(config))


def rpc(client: TestClient, method: str, params: dict | None = None, request_id: int = 1):
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        headers={"Mcp-Method": method},
    )


def test_capture_to_durable_memory_pipeline(tmp_path):
    durable_page_id = "services/aurora-memory"
    unique_term = "AuroraVectorHandshake"
    capture_date = "2026-05-22"

    with make_client(tmp_path) as client:
        seed = client.put(
            f"/api/pages/{durable_page_id}",
            json={
                "content": f"""---
title: Aurora Memory
kind: service
visibility: internal
status: active
summary: Durable service page for capture-to-memory regression coverage.
sources:
  - tests/test_capture_to_durable.py
---

# Aurora Memory

## Facts

Aurora Memory stores durable facts for regression tests.

See [[dailies/{capture_date}]].
""",
            },
        )
        assert seed.status_code == 200, seed.text

        captured = client.post(
            "/api/memory/capture",
            json={
                "text": f"{unique_term} records consolidated durable memory through Lore.",
                "agent_name": "codex",
                "task_id": "flow_000598",
                "lane": "project",
                "metadata": {
                    "title": "Aurora durable memory capture",
                    "capture_date": capture_date,
                    "confidence": "high",
                    "suggested_target_page": durable_page_id,
                    "sources": ["tests/test_capture_to_durable.py"],
                    "evidence": f"{unique_term} appeared in the capture payload.",
                },
                "tool_calls": [{"tool": "pytest", "target": "capture-to-durable"}],
                "constraints": ["offline-only"],
                "policies_applied": ["L-MEM-05"],
            },
        )
        assert captured.status_code == 201, captured.text
        capture_id = captured.json()["capture_id"]

        capture_page = client.get(f"/api/pages/{capture_id}")
        assert capture_page.status_code == 200, capture_page.text
        capture_detail = capture_page.json()
        assert capture_detail["frontmatter"]["kind"] == "capture"
        assert capture_detail["frontmatter"]["status"] == "draft"
        assert capture_detail["frontmatter"]["source_task"] == "flow_000598"
        assert capture_detail["frontmatter"]["confidence"] == "high"
        assert capture_detail["tool_calls"] == [{"tool": "pytest", "target": "capture-to-durable"}]
        assert capture_detail["constraints"] == ["offline-only"]
        assert capture_detail["policies_applied"] == ["L-MEM-05"]

        dry_extraction = client.post(
            "/api/extraction/run",
            json={"dry_run": True, "capture_ids": [capture_id], "batch_size": 10},
        )
        assert dry_extraction.status_code == 200, dry_extraction.text
        dry_payload = dry_extraction.json()
        assert dry_payload["source_capture_ids"] == [capture_id]
        assert dry_payload["claims"] or dry_payload["edges"] or dry_payload["entities"] or dry_payload["invalidations"]

        consolidation = client.post(
            "/api/consolidation/run",
            json={"dry_run": False, "batch_size": 10, "max_auto_apply": 0},
        )
        assert consolidation.status_code == 200, consolidation.text
        consolidation_payload = consolidation.json()
        assert consolidation_payload["captures_processed"] == 1
        assert consolidation_payload["candidates_extracted"] >= 1
        assert consolidation_payload["plans_generated"] >= 1

        candidates = client.get("/api/extraction/candidates", params={"status": "candidate"})
        assert candidates.status_code == 200, candidates.text
        assert any(capture_id in candidate["source_capture_ids"] for candidate in candidates.json()["candidates"])

        plans = client.get("/api/consolidation/plans")
        assert plans.status_code == 200, plans.text
        plan_rows = plans.json()["plans"]
        plan = next(plan for plan in plan_rows if plan["target_page_id"] == durable_page_id)

        applied = client.post(f"/api/consolidation/apply/{plan['plan_id']}", json={})
        assert applied.status_code == 200, applied.text
        assert applied.json()["target_page_id"] == durable_page_id

        durable_page = client.get(f"/api/pages/{durable_page_id}")
        assert durable_page.status_code == 200, durable_page.text
        durable_detail = durable_page.json()
        assert durable_detail["kind"] != "capture"
        assert durable_detail["frontmatter"]["kind"] == "service"
        assert unique_term in durable_detail["content"]

        distilled = client.post(
            "/api/distill/daily",
            json={"date": capture_date, "actor": "codex"},
        )
        assert distilled.status_code == 200, distilled.text
        distilled_payload = distilled.json()
        assert distilled_payload["page_id"] == f"dailies/{capture_date}"
        assert distilled_payload["capture_count"] >= 1

        daily_page = client.get(f"/api/pages/dailies/{capture_date}")
        assert daily_page.status_code == 200, daily_page.text
        assert daily_page.json()["frontmatter"]["kind"] == "daily-note"

        reindexed = client.post("/api/search/reindex")
        assert reindexed.status_code == 200, reindexed.text

        searched = client.get("/api/search", params={"q": unique_term})
        assert searched.status_code == 200, searched.text
        assert any(hit["page"]["id"] == durable_page_id for hit in searched.json()["hits"])

        rag = rpc(
            client,
            "tools/call",
            {"name": "lore_rag_context", "arguments": {"query": unique_term, "limit": 5}},
        )
        assert rag.status_code == 200, rag.text
        rag_result = rag.json()["result"]
        assert rag_result["isError"] is False
        assert any(
            result["page_id"] == durable_page_id
            and any(unique_term in citation for citation in result.get("citations", []))
            for result in rag_result["structuredContent"]["results"]
        )

        mcp_search = rpc(
            client,
            "tools/call",
            {"name": "lore_search", "arguments": {"query": unique_term, "limit": 5}},
        )
        assert mcp_search.status_code == 200, mcp_search.text
        assert any(
            hit["page_id"] == durable_page_id for hit in mcp_search.json()["result"]["structuredContent"]["hits"]
        )

        heartbeat = client.get("/api/heartbeat")
        assert heartbeat.status_code == 200, heartbeat.text
        heartbeat_payload = heartbeat.json()
        issue_page_ids = {
            item["page_id"]
            for category in (
                "stale_pages",
                "missing_metadata",
                "contradictions",
                "low_confidence",
                "expired_facts",
                "procedure_issues",
            )
            for item in heartbeat_payload[category]["items"]
        }
        assert durable_page_id not in issue_page_ids
        assert f"dailies/{capture_date}" not in issue_page_ids

        mcp_capture = rpc(
            client,
            "tools/call",
            {
                "name": "lore_capture",
                "arguments": {
                    "title": "MCP durable memory capture",
                    "observation": f"MCP capture also records {unique_term} follow-up context.",
                    "agent": "codex",
                    "capture_date": capture_date,
                    "task_id": "flow_000598-mcp",
                    "confidence": "high",
                    "suggested_target_page": durable_page_id,
                },
            },
        )
        assert mcp_capture.status_code == 200, mcp_capture.text
        mcp_capture_id = mcp_capture.json()["result"]["structuredContent"]["page"]["id"]

        listed = rpc(
            client,
            "tools/call",
            {"name": "lore_list_captures", "arguments": {"status": "all", "limit": 20}},
        )
        assert listed.status_code == 200, listed.text
        listed_ids = {capture["id"] for capture in listed.json()["result"]["structuredContent"]["captures"]}
        assert capture_id in listed_ids
        assert mcp_capture_id in listed_ids
