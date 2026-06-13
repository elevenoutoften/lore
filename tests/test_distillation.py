from __future__ import annotations

from datetime import UTC, datetime


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def test_promote_daily_note_preserves_frontmatter(client):
    """Promoting a daily note must keep its frontmatter, not corrupt it.

    Regression for the doubled '---' fence that demoted all frontmatter keys
    into the body, losing kind/status and the 'status: promoted' contract.
    """
    today = _today()
    created = client.post("/api/capture", json={"observation": "Daily distill check.", "title": "Distill Check"})
    assert created.status_code == 201, created.text

    distilled = client.post("/api/distill/daily", json={"date": today})
    assert distilled.status_code == 200, distilled.text
    assert distilled.json()["capture_count"] >= 1

    promoted = client.post(f"/api/distill/promote/{today}")
    assert promoted.status_code == 200, promoted.text

    page = client.get(f"/api/pages/dailies/{today}")
    assert page.status_code == 200, page.text
    frontmatter = page.json()["frontmatter"]
    assert frontmatter["kind"] == "daily-note"
    assert frontmatter["status"] == "promoted"
    assert frontmatter.get("reviewed_at")


def test_redistill_does_not_revert_promoted_note(client):
    """Re-distilling an already-promoted day must not silently revert it to active."""
    today = _today()
    created = client.post("/api/capture", json={"observation": "Re-distill check.", "title": "Redistill Check"})
    assert created.status_code == 201, created.text

    assert client.post("/api/distill/daily", json={"date": today}).status_code == 200
    assert client.post(f"/api/distill/promote/{today}").status_code == 200

    before = client.get(f"/api/pages/dailies/{today}").json()["frontmatter"]
    reviewed_at = before["reviewed_at"]

    redistilled = client.post("/api/distill/daily", json={"date": today})
    assert redistilled.status_code == 200, redistilled.text

    after = client.get(f"/api/pages/dailies/{today}").json()["frontmatter"]
    assert after["status"] == "promoted"
    assert after["reviewed_at"] == reviewed_at
