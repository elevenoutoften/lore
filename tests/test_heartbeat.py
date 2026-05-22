from __future__ import annotations


def test_heartbeat_dashboard_includes_consolidation_section(client):
    response = client.get("/heartbeat")

    assert response.status_code == 200
    html = response.text
    assert "Consolidation" in html
    assert "/api/memory/health" in html
    assert "memory-health-last-consolidation" in html
