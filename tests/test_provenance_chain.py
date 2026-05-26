from __future__ import annotations


def test_round_trip_provenance_chain(client):
    unique_term = "prov-chain-token-9d41e7"
    observed_at = "2026-05-26T12:34:56+00:00"
    valid_from = "2026-05-01"
    valid_until = "2026-12-31"
    actor = "nyx"
    lane = "project"
    source_path = "docs/provenance-chain.md"
    source_url = "https://example.com/provenance-chain"

    capture = client.post(
        "/api/capture",
        json={
            "title": "Provenance chain capture",
            "observation": (
                f"{unique_term} confirms Lore provenance behavior and references "
                "[[services/workflow-engine]]."
            ),
            "capture_date": "2026-05-26",
            "suggested_target_page": "services/lore-provenance-chain",
            "confidence": "high",
            "actor": actor,
            "lane": lane,
            "observed_at": observed_at,
            "valid_from": valid_from,
            "valid_until": valid_until,
            "source_paths": [source_path],
            "source_urls": [source_url],
        },
    )
    assert capture.status_code == 201, capture.text
    capture_page = capture.json()["page"]
    capture_id = capture_page["id"]
    assert capture_page["frontmatter"]["observed_at"] == observed_at
    assert capture_page["frontmatter"]["actor"] == actor
    assert capture_page["frontmatter"]["lane"] == lane

    extract = client.post("/api/extraction/run", json={"capture_ids": [capture_id], "dry_run": False})
    assert extract.status_code == 200, extract.text
    extract_payload = extract.json()
    assert extract_payload["source_capture_ids"] == [capture_id]
    claim = extract_payload["claims"][0]
    assert claim["actor"] == actor
    assert claim["lane"] == lane
    assert claim["observed_at"] == observed_at
    assert claim["valid_from"] == valid_from
    assert claim["valid_until"] == valid_until

    candidates = client.get(
        "/api/ledger/candidates",
        params={"type": "claim", "capture_id": capture_id, "limit": 20},
    )
    assert candidates.status_code == 200, candidates.text
    candidate = next(
        item
        for item in candidates.json()
        if capture_id in item["source_capture_ids"] and item["content"]["object"] == claim["object"]
    )
    candidate_id = candidate["candidate_id"]
    assert candidate["actor"] == actor
    assert candidate["lane"] == lane
    assert candidate["observed_at"] == observed_at
    assert candidate["valid_from"] == valid_from
    assert candidate["valid_until"] == valid_until

    graph = client.get("/api/context-graph")
    assert graph.status_code == 200, graph.text
    candidate_node = next(node for node in graph.json()["nodes"] if node["id"] == f"candidate:{candidate_id}")
    assert candidate_node["metadata"]["actor"] == actor
    assert candidate_node["metadata"]["lane"] == lane
    assert candidate_node["metadata"]["observed_at"] == observed_at
    assert candidate_node["metadata"]["valid_from"] == valid_from
    assert candidate_node["metadata"]["valid_until"] == valid_until

    reindex = client.post("/api/search/reindex")
    assert reindex.status_code == 200, reindex.text

    search = client.get("/api/search", params={"q": unique_term, "limit": 10})
    assert search.status_code == 200, search.text
    search_hit = next(hit for hit in search.json()["hits"] if hit["page"]["id"] == capture_id)
    assert search_hit["observed_at"] == observed_at
    assert search_hit["valid_from"] == valid_from
    assert search_hit["valid_until"] == valid_until
    assert search_hit["actor"] == actor
    assert search_hit["lane"] == lane
    assert source_path in search_hit["source_refs"]
    assert source_url in search_hit["source_refs"]

    rag = client.post("/api/rag/retrieve", json={"query": unique_term, "limit": 10})
    assert rag.status_code == 200, rag.text
    rag_result = next(result for result in rag.json()["results"] if result["page_id"] == capture_id)
    assert rag_result["observed_at"] == observed_at
    assert rag_result["valid_from"] == valid_from
    assert rag_result["valid_until"] == valid_until
    assert rag_result["actor"] == actor
    assert rag_result["lane"] == lane
    assert source_path in rag_result["source_refs"]
    assert source_url in rag_result["source_refs"]

    trace = client.post(
        "/api/traces",
        json={
            "actor": actor,
            "reason_summary": "Verify provenance lane round-trips through traces.",
            "lane": lane,
            "context_refs": [
                {"type": "capture", "id": capture_id},
                {"type": "candidate", "id": candidate_id},
            ],
            "related_ids": {"capture_id": capture_id, "candidate_id": candidate_id},
        },
    )
    assert trace.status_code == 201, trace.text
    trace_id = trace.json()["trace_id"]
    assert trace.json()["lane"] == lane

    trace_get = client.get(f"/api/traces/{trace_id}")
    assert trace_get.status_code == 200, trace_get.text
    assert trace_get.json()["lane"] == lane
