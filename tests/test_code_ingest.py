from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lore_app.code_ingest.fastapi_ingest import ingest_fastapi_routes
from lore_app.code_ingest.ingest_service import ingest_service_code
from lore_app.code_ingest.source_refs import resolve_source_ref
from lore_app.code_ingest.symbol_ingest import ingest_python_symbols
from lore_app.code_ingest.validate import (
    IngestValidationError,
    safe_rglob_py_files,
    validate_service_id,
    validate_source_dir,
)
from lore_app.config import LoreConfig
from lore_app.main import create_app


def _symlink_or_skip(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")


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


def test_fastapi_route_ingest_records_all_methods(tmp_path):
    """A .route() with multiple methods must record every method, not just the first."""
    app_file = tmp_path / "app.py"
    app_file.write_text(
        """from fastapi import FastAPI

app = FastAPI()

@app.route("/submit", methods=["POST", "PUT"])
async def submit():
    return {}
""",
        encoding="utf-8",
    )

    routes = ingest_fastapi_routes(tmp_path)

    assert {(route.method, route.path) for route in routes} == {("POST", "/submit"), ("PUT", "/submit")}
    assert routes[0].line_number == 6


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
    _, admin_key = client.app.state.api_key_store.create_key(name="code-ingest-admin", role="admin")
    admin_headers = {"Authorization": f"Bearer {admin_key}"}

    api_response = client.post(
        "/api/code-ingest/services/workflow-engine",
        params={"source_dir": str(tmp_path)},
        headers=admin_headers,
    )
    assert api_response.status_code == 200, api_response.text
    assert api_response.json()["routes"][0]["path"] == "/ready"

    inventory_response = client.get("/api/code-ingest/services/workflow-engine/inventory", headers=admin_headers)
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


def test_code_ingest_rest_requires_admin(content_dir, search_db, tmp_path):
    """Code ingest REST endpoints require an admin API key (with auth enabled)."""
    source_dir = tmp_path / "svc"
    source_dir.mkdir()
    (source_dir / "app.py").write_text("def ready():\n    return True\n", encoding="utf-8")

    config = LoreConfig()
    config.content_dir = content_dir
    config.search_db = search_db
    config.vector_db = search_db.with_name("vectors.db")
    config.ledger_db = search_db.with_name("ledger.db")
    config.api_keys_db = search_db.with_name("api_keys.db")
    config.settings_db = search_db.with_name("settings.db")
    config.auth_mode = "api_key"
    config.auto_consolidate = False
    config.code_ingest_roots = [tmp_path]
    app = create_app(config)
    _, admin_key = app.state.api_key_store.create_key(name="rest-admin", role="admin")
    _, writer_key = app.state.api_key_store.create_key(name="rest-writer", role="writer")
    admin_headers = {"Authorization": f"Bearer {admin_key}"}
    writer_headers = {"Authorization": f"Bearer {writer_key}"}

    with TestClient(app) as client:
        unauthenticated = client.post("/api/code-ingest/services/admin-only", params={"source_dir": str(source_dir)})
        writer_post = client.post(
            "/api/code-ingest/services/admin-only", params={"source_dir": str(source_dir)}, headers=writer_headers
        )
        writer_inventory = client.get("/api/code-ingest/services/admin-only/inventory", headers=writer_headers)
        admin_post = client.post(
            "/api/code-ingest/services/admin-only", params={"source_dir": str(source_dir)}, headers=admin_headers
        )
        admin_inventory = client.get("/api/code-ingest/services/admin-only/inventory", headers=admin_headers)

    assert unauthenticated.status_code == 401
    assert writer_post.status_code == 403
    assert writer_inventory.status_code == 403
    assert admin_post.status_code == 200, admin_post.text
    assert admin_inventory.status_code == 200


def test_code_ingest_mcp_requires_admin_role(content_dir, search_db, tmp_path):
    """Writer API keys must not call MCP code ingest."""
    source_dir = tmp_path / "service"
    source_dir.mkdir()
    (source_dir / "app.py").write_text("def ready():\n    return True\n", encoding="utf-8")

    config = LoreConfig()
    config.content_dir = content_dir
    config.search_db = search_db
    config.vector_db = search_db.with_name("vectors.db")
    config.ledger_db = search_db.with_name("ledger.db")
    config.api_keys_db = search_db.with_name("api_keys.db")
    config.auth_mode = "api_key"
    config.code_ingest_roots = [tmp_path]
    app = create_app(config)
    _, admin_key = app.state.api_key_store.create_key(name="mcp-admin", role="admin")
    _, writer_key = app.state.api_key_store.create_key(name="mcp-writer", role="writer")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "lore_ingest_service",
            "arguments": {"service_id": "services/mcp-admin-only", "source_dir": str(source_dir)},
        },
    }

    with TestClient(app) as api_client:
        writer_response = api_client.post(
            "/mcp",
            json=payload,
            headers={"Authorization": f"Bearer {writer_key}"},
        )
        admin_response = api_client.post(
            "/mcp",
            json=payload,
            headers={"Authorization": f"Bearer {admin_key}"},
        )

    assert writer_response.status_code == 200
    assert writer_response.json()["error"]["message"] == "Admin access required for code ingest."
    assert admin_response.status_code == 200, admin_response.text
    assert admin_response.json()["result"]["structuredContent"]["service_id"] == "services/mcp-admin-only"


def test_parse_roots_windows_drive_letters(monkeypatch):
    """Windows root parsing must not split drive letters on colon."""
    monkeypatch.setattr("lore_app.config.platform.system", lambda: "Windows")

    result = LoreConfig._parse_roots(r"D:\Projects;E:\Code")

    assert len(result) == 2
    assert str(result[0]).endswith(r"D:\Projects")
    assert str(result[1]).endswith(r"E:\Code")


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
    allowed_root = tmp_path / "allowed"
    outside_root = tmp_path / "outside_root"
    allowed_root.mkdir()
    outside_root.mkdir()
    monkeypatch.setenv("LORE_CODE_INGEST_ROOTS", str(allowed_root))
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    config = LoreConfig()
    (tmp_path / "pages").mkdir(exist_ok=True)
    link = allowed_root / "evil_link"
    _symlink_or_skip(link, outside_root, target_is_directory=True)
    with pytest.raises(IngestValidationError, match="outside"):
        validate_source_dir(str(link), config)


def test_symlink_file_inside_root_is_skipped(tmp_path, monkeypatch):
    """A .py symlink inside an allowed root pointing outside is skipped."""
    allowed_root = tmp_path / "allowed"
    outside_root = tmp_path / "outside_root"
    allowed_root.mkdir()
    outside_root.mkdir()
    monkeypatch.setenv("LORE_CODE_INGEST_ROOTS", str(allowed_root))
    monkeypatch.setenv("LORE_CONTENT_DIR", str(tmp_path / "pages"))
    config = LoreConfig()
    (tmp_path / "pages").mkdir(exist_ok=True)
    (allowed_root / "real.py").write_text("def hello(): pass", encoding="utf-8")
    (outside_root / "secret.py").write_text("def leaked(): pass", encoding="utf-8")
    _symlink_or_skip(allowed_root / "evil.py", outside_root / "secret.py")

    result = validate_source_dir(str(allowed_root), config)
    assert result == allowed_root.resolve()

    safe_files = safe_rglob_py_files(allowed_root, config.code_ingest_roots)
    safe_names = {path.name for path in safe_files}
    assert "real.py" in safe_names
    assert "evil.py" not in safe_names

    symbols = ingest_python_symbols(allowed_root)
    symbol_names = {symbol.name for symbol in symbols}
    assert "hello" in symbol_names
    assert "leaked" not in symbol_names

    inventory = ingest_service_code("services/demo", allowed_root)
    source_file_names = {Path(source_ref.file_path).name for source_ref in inventory.source_files}
    assert "real.py" in source_file_names
    assert "evil.py" not in source_file_names


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
