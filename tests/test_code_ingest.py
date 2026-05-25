from __future__ import annotations

from pathlib import Path

import pytest

from lore_app.code_ingest.config_ingest import ingest_caddyfile, ingest_docker_compose, ingest_systemd_units
from lore_app.code_ingest.fastapi_ingest import ingest_fastapi_routes
from lore_app.code_ingest.ingest_service import ingest_service_code
from lore_app.code_ingest.source_refs import resolve_source_ref
from lore_app.code_ingest.symbol_ingest import ingest_python_symbols
from lore_app.code_ingest.validate import validate_source_dir, validate_service_id, IngestValidationError
from lore_app.config import LoreConfig


def test_fastapi_route_ingest(tmp_path):
    app_file = tmp_path / "app.py"
    app_file.write_text(
        """from fastapi import FastAPI

app = FastAPI()

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.route("/submit", methods=["POST"])
async def submit():
    return {}
""",
        encoding="utf-8",
    )

    routes = ingest_fastapi_routes(tmp_path)

    assert [(route.method, route.path, route.function_name) for route in routes] == [
        ("GET", "/healthz", "healthz"),
        ("POST", "/submit", "submit"),
    ]
    assert routes[0].file_path == str(app_file)
    assert routes[0].line_number == 6


def test_config_ingesters(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """services:
  lore:
    image: lore/lore:latest
    ports:
      - "8080:8000"
    depends_on:
      - db
  db:
    image: postgres:17
""",
        encoding="utf-8",
    )
    caddyfile = tmp_path / "Caddyfile"
    caddyfile.write_text("lore.example.com {\n  reverse_proxy lore:8000\n}\n", encoding="utf-8")
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    (unit_dir / "lore.service").write_text("[Service]\nExecStart=/usr/bin/lore\n", encoding="utf-8")

    compose_specs = ingest_docker_compose(compose)
    caddy_specs = ingest_caddyfile(caddyfile)
    systemd_specs = ingest_systemd_units(unit_dir)

    assert compose_specs[0].name == "lore"
    assert compose_specs[0].image == "lore/lore:latest"
    assert compose_specs[0].ports == ["8080:8000"]
    assert compose_specs[0].depends_on == ["db"]
    assert caddy_specs[0].name == "lore"
    assert caddy_specs[0].ports == ["8000"]
    assert systemd_specs[0].name == "lore"
    assert systemd_specs[0].kind == "systemd"


def test_source_ref_resolution(tmp_path):
    source = tmp_path / "services" / "lore" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("def app():\n    pass\n", encoding="utf-8")

    resolved = resolve_source_ref("services/lore/main.py:1-2::app", repo_root=tmp_path)

    assert resolved.file_path == str(source)
    assert resolved.line_start == 1
    assert resolved.line_end == 2
    assert resolved.symbol == "app"
    assert resolved.language == "python"


def test_symbol_and_service_inventory(tmp_path):
    (tmp_path / "module.py").write_text(
        '''class Worker:
    """Runs jobs."""

    def run(self):
        """Run one job."""
        return True

def helper():
    return None
''',
        encoding="utf-8",
    )

    symbols = ingest_python_symbols(tmp_path)
    inventory = ingest_service_code("services/demo", tmp_path)

    klass = next(symbol for symbol in symbols if symbol.name == "Worker")
    assert klass.kind == "class"
    assert klass.exports == ["run"]
    assert any(symbol.name == "helper" and symbol.kind == "function" for symbol in symbols)
    assert inventory.service_id == "services/demo"
    assert len(inventory.symbols) == len(symbols)
    assert inventory.ingested_at is not None


def test_code_references_endpoint(client):
    markdown = """---
title: Lore
kind: service
visibility: internal
sources:
  - services/lore/lore_app/main.py
source_paths:
  - services/lore/lore_app/mcp.py
---

# Lore

Body mentions services/lore/lore_app/code_ingest/fastapi_ingest.py.
"""
    client.put("/api/pages/services/lore", json={"content": markdown})

    source_result = client.get("/api/code-references/services/lore/lore_app/main.py").json()
    source_path_result = client.get("/api/code-references/services/lore/lore_app/mcp.py").json()
    body_result = client.get("/api/code-references/code_ingest/fastapi_ingest.py").json()

    assert source_result["referenced_by"][0]["match_field"] == "sources"
    assert source_path_result["referenced_by"][0]["match_field"] == "source_paths"
    assert body_result["referenced_by"][0]["match_field"] == "body"


def test_code_ingest_api_and_mcp(client, tmp_path):
    (tmp_path / "app.py").write_text(
        """from fastapi import FastAPI

app = FastAPI()

@app.get("/ready")
def ready():
    return {"ready": True}
""",
        encoding="utf-8",
    )

    api_response = client.post(
        "/api/code-ingest/services/workflow-engine",
        params={"source_dir": str(tmp_path)},
    )
    assert api_response.status_code == 200, api_response.text
    assert api_response.json()["routes"][0]["path"] == "/ready"

    inventory_response = client.get("/api/code-ingest/services/workflow-engine/inventory")
    assert inventory_response.status_code == 200
    assert inventory_response.json()["service_id"] == "services/workflow-engine"

    mcp_response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "lore_ingest_service",
                "arguments": {"service_id": "services/workflow-engine", "source_dir": str(tmp_path)},
            },
        },
    )
    assert mcp_response.status_code == 200
    mcp_payload = mcp_response.json()["result"]
    assert mcp_payload["structuredContent"]["routes"][0]["function_name"] == "ready"
    assert "Ingested services/workflow-engine code inventory" in mcp_payload["content"][0]["text"]


# ── L-SEC-10: Validation tests ──────────────────────────────────


def test_roots_empty_disables_ingest(tmp_path, monkeypatch):
    """When LORE_CODE_INGEST_ROOTS is empty, ingest is disabled."""
    monkeypatch.delenv("LORE_CODE_INGEST_ROOTS", raising=False)
    config = LoreConfig()
    config.code_ingest_roots = []  # explicit
    with pytest.raises(IngestValidationError, match="disabled"):
        validate_source_dir(tmp_path, config)


def test_allowed_path_success(tmp_path, monkeypatch):
    """source_dir inside an allowed root passes validation."""
    monkeypatch.setenv("LORE_CODE_INGEST_ROOTS", str(tmp_path))
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    config = LoreConfig()
    (tmp_path / "pages").mkdir(exist_ok=True)
    subdir = tmp_path / "myproject"
    subdir.mkdir()
    (subdir / "app.py").write_text("x = 1", encoding="utf-8")
    result = validate_source_dir(str(subdir), config)
    assert result.is_relative_to(tmp_path)


def test_escape_attempt_rejected(tmp_path, monkeypatch):
    """source_dir outside all allowed roots is rejected."""
    monkeypatch.setenv("LORE_CODE_INGEST_ROOTS", str(tmp_path))
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    config = LoreConfig()
    (tmp_path / "pages").mkdir(exist_ok=True)
    with pytest.raises(IngestValidationError, match="outside"):
        validate_source_dir("/etc", config)


def test_symlink_escape_rejected(tmp_path, monkeypatch):
    """symlink pointing outside allowed roots is rejected after resolve."""
    monkeypatch.setenv("LORE_CODE_INGEST_ROOTS", str(tmp_path))
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    config = LoreConfig()
    (tmp_path / "pages").mkdir(exist_ok=True)
    # Create symlink inside allowed root pointing to /etc
    link = tmp_path / "evil_link"
    link.symlink_to("/etc")
    with pytest.raises(IngestValidationError, match="outside"):
        validate_source_dir(str(link), config)


def test_file_count_limit_rejected(tmp_path, monkeypatch):
    """source_dir exceeding file count limit is rejected."""
    monkeypatch.setenv("LORE_CODE_INGEST_ROOTS", str(tmp_path))
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    monkeypatch.setenv("LORE_CODE_INGEST_MAX_FILES", "3")
    config = LoreConfig()
    (tmp_path / "pages").mkdir(exist_ok=True)
    subdir = tmp_path / "big_project"
    subdir.mkdir()
    for i in range(5):
        (subdir / f"file{i}.py").write_text("x = 1", encoding="utf-8")
    with pytest.raises(IngestValidationError, match="5 Python files"):
        validate_source_dir(str(subdir), config)


def test_service_id_rejected_for_invalid():
    """service_id with path traversal or special chars is rejected."""
    with pytest.raises(IngestValidationError):
        validate_service_id("../../etc/passwd")
    with pytest.raises(IngestValidationError):
        validate_service_id("my|service")


def test_service_id_accepted_for_valid():
    """Valid service_id passes validation."""
    assert validate_service_id("services/workflow-engine") == "services/workflow-engine"
    assert validate_service_id("my_service") == "my_service"
