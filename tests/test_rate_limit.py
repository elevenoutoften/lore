"""Tests for L-SEC-11: default-deny rate limiting on all write endpoints."""

from lore_app.security import RateLimiter


def test_rate_limit_applies_to_extraction_run(client):
    """Previously-missed POST /api/extraction/run is now rate-limited."""
    client.app.state.write_rate_limiter = RateLimiter(max_requests=1, window_seconds=60)
    # First request
    r1 = client.post("/api/extraction/run", json={"capture_ids": None, "batch_size": 5, "dry_run": True})
    # Whether it succeeds or fails for business reasons, the first request should not be 429
    assert r1.status_code != 429
    # Second request should be rate-limited
    r2 = client.post("/api/extraction/run", json={"capture_ids": None, "batch_size": 5, "dry_run": True})
    assert r2.status_code == 429


def test_rate_limit_applies_to_traces_post(client):
    """POST /api/traces is now rate-limited."""
    client.app.state.write_rate_limiter = RateLimiter(max_requests=1, window_seconds=60)
    r1 = client.post("/api/traces", json={"actor": "test", "reason_summary": "test trace"})
    assert r1.status_code != 429
    r2 = client.post("/api/traces", json={"actor": "test", "reason_summary": "test trace"})
    assert r2.status_code == 429


def test_rate_limit_applies_to_consolidation_run(client):
    """POST /api/consolidation/run is now rate-limited."""
    client.app.state.write_rate_limiter = RateLimiter(max_requests=1, window_seconds=60)
    r1 = client.post("/api/consolidation/run", json={"dry_run": True, "batch_size": 5})
    assert r1.status_code != 429
    r2 = client.post("/api/consolidation/run", json={"dry_run": True, "batch_size": 5})
    assert r2.status_code == 429


def test_rate_limit_applies_to_policies_post(client):
    """POST /api/policies is now rate-limited."""
    client.app.state.write_rate_limiter = RateLimiter(max_requests=1, window_seconds=60)
    r1 = client.post("/api/policies", json={"policy_id": "test-policy:v1", "name": "Test Policy"})
    assert r1.status_code != 429
    r2 = client.post("/api/policies", json={"policy_id": "test-policy:v1", "name": "Test Policy"})
    assert r2.status_code == 429


def test_rate_limit_applies_to_policies_delete(client):
    """DELETE /api/policies/{id} is now rate-limited."""
    client.app.state.write_rate_limiter = RateLimiter(max_requests=1, window_seconds=60)
    r1 = client.delete("/api/policies/nonexistent-policy")
    # First request should not be 429 (it might 404, but that's fine)
    assert r1.status_code != 429
    r2 = client.delete("/api/policies/nonexistent-policy")
    assert r2.status_code == 429


def test_rate_limit_applies_to_extraction_reset(client):
    """POST /api/extraction/reset is now rate-limited."""
    client.app.state.write_rate_limiter = RateLimiter(max_requests=1, window_seconds=60)
    r1 = client.post("/api/extraction/reset", json={"capture_ids": None})
    assert r1.status_code != 429
    r2 = client.post("/api/extraction/reset", json={"capture_ids": None})
    assert r2.status_code == 429


def test_rate_limit_applies_to_traces_patch(client):
    """PATCH /api/traces/{id} is now rate-limited."""
    client.app.state.write_rate_limiter = RateLimiter(max_requests=1, window_seconds=60)
    r1 = client.patch("/api/traces/nonexistent-trace", json={"status": "completed"})
    assert r1.status_code != 429
    r2 = client.patch("/api/traces/nonexistent-trace", json={"status": "completed"})
    assert r2.status_code == 429


def test_get_endpoints_not_rate_limited(client):
    """GET endpoints under /api/ should never be rate-limited."""
    client.app.state.write_rate_limiter = RateLimiter(max_requests=0, window_seconds=60)
    r = client.get("/api/pages")
    assert r.status_code != 429
    r = client.get("/api/extraction/status")
    assert r.status_code != 429
    r = client.get("/api/policies")
    assert r.status_code != 429


def test_rate_limit_keys_on_agent_identity_for_independent_budgets(client):
    """Distinct agents get independent write budgets instead of sharing one IP bucket."""
    from lore_app.security import RateLimiter

    client.app.state.write_rate_limiter = RateLimiter(max_requests=1, window_seconds=60)
    body = {"actor": "test", "reason_summary": "trace"}
    agent_a = {"Authorization": "Bearer agent-aaa-token"}
    agent_b = {"Authorization": "Bearer agent-bbb-token"}

    assert client.post("/api/traces", json=body, headers=agent_a).status_code != 429
    assert client.post("/api/traces", json=body, headers=agent_a).status_code == 429  # same agent exhausted
    assert client.post("/api/traces", json=body, headers=agent_b).status_code != 429  # different agent, own budget
