from __future__ import annotations

import gzip

from lore_app.audit import AuditEntry, AuditLog


def test_page_writes_record_audit_entries(client):
    markdown = "---\ntitle: Audit Target\nkind: page\nvisibility: internal\n---\n\n# Audit Target\n"

    created = client.put(
        "/api/pages/audit/target",
        json={"content": markdown},
        headers={"X-Lore-Actor": "agent:codex"},
    )
    assert created.status_code == 200, created.text

    updated = client.put(
        "/api/pages/audit/target",
        json={"content": markdown + "\nUpdated.\n"},
        headers={"X-Lore-Actor": "user:alice"},
    )
    assert updated.status_code == 200, updated.text

    deleted = client.delete("/api/pages/audit/target", headers={"X-Lore-Actor": "agent:codex"})
    assert deleted.status_code == 204

    audit = client.get("/api/audit", params={"page_id": "audit/target"}).json()
    assert [entry["operation"] for entry in audit] == ["delete", "update", "create"]
    assert audit[0]["actor"] == "agent:codex"
    assert audit[1]["actor"] == "user:alice"
    assert audit[0]["diff_size"] is not None


def test_page_history_filters_to_page(client):
    markdown = "---\ntitle: History Target\nkind: page\n---\n\n# History Target\n"
    response = client.put(
        "/api/pages/history/target",
        json={"content": markdown},
        headers={"X-Lore-Actor": "agent:codex"},
    )
    assert response.status_code == 200

    history = client.get("/api/pages/history/target/history")
    assert history.status_code == 200
    payload = history.json()
    assert len(payload) == 1
    assert payload[0]["page_id"] == "history/target"
    assert payload[0]["operation"] == "create"


def test_capture_and_promotion_record_audit(client):
    captured = client.post(
        "/api/capture",
        json={
            "title": "Audit capture",
            "observation": "This capture should be audited.",
            "capture_date": "2026-05-01",
            "suggested_target_page": "services/audit-promoted",
        },
        headers={"X-Lore-Actor": "agent:codex"},
    )
    assert captured.status_code == 201, captured.text
    capture_id = captured.json()["page"]["id"]

    promoted = client.post(
        f"/api/captures/{capture_id}/promote",
        json={},
        headers={"X-Lore-Actor": "user:alice"},
    )
    assert promoted.status_code == 200, promoted.text

    audit = client.get("/api/audit").json()
    operations = {(entry["operation"], entry["page_id"], entry["actor"]) for entry in audit}
    assert ("capture", capture_id, "agent:codex") in operations
    assert ("promote", "services/audit-promoted", "user:alice") in operations


def test_metadata_update_and_stub_creation_record_audit(client):
    metadata = client.patch(
        "/api/pages/projects/example-project/metadata",
        json={"owner": "platform"},
        headers={"X-Lore-Actor": "user:alice"},
    )
    assert metadata.status_code == 200

    stub = client.post(
        "/api/pages/services/audited-stub/stub",
        json={"source_page": "projects/example-project"},
        headers={"X-Lore-Actor": "agent:codex"},
    )
    assert stub.status_code == 201

    audit = client.get("/api/audit").json()
    operations = {(entry["operation"], entry["page_id"], entry["actor"]) for entry in audit}
    assert ("metadata_update", "projects/example-project", "user:alice") in operations
    assert ("stub_create", "services/audited-stub", "agent:codex") in operations


def test_audit_log_compacts_and_reads_gzip(tmp_path):
    log = AuditLog(tmp_path, retention_days=1)
    old_path = tmp_path / "2020-01-01.jsonl"
    old_path.write_text(
        '{"actor": "agent", "commit_ref": null, "diff_size": 1, "operation": "create", '
        '"page_id": "old/page", "summary": "old", "timestamp": "2020-01-01T00:00:00+00:00"}\n',
        encoding="utf-8",
    )

    log.record(
        AuditEntry(
            timestamp="2026-05-05T00:00:00+00:00",
            actor="agent",
            operation="create",
            page_id="new/page",
            summary="new",
        )
    )

    gz_path = tmp_path / "2020-01-01.jsonl.gz"
    assert gz_path.exists()
    assert not old_path.exists()
    with gzip.open(gz_path, "rt", encoding="utf-8") as handle:
        assert "old/page" in handle.read()
    assert [entry.page_id for entry in log.query(limit=10)] == ["new/page", "old/page"]
