from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .fastapi_ingest import ingest_fastapi_routes
from .schemas import ServiceInventory
from .source_refs import resolve_source_refs
from .symbol_ingest import ingest_python_symbols
from .validate import safe_rglob_py_files


def ingest_service_code(
    service_id: str,
    source_dir: str | Path,
    repo_root: str | Path | None = None,
) -> ServiceInventory:
    """Run all ingesters for a service's source code directory."""

    source_dir = Path(source_dir)
    routes = ingest_fastapi_routes(source_dir)
    symbols = ingest_python_symbols(source_dir)
    safe_files = safe_rglob_py_files(source_dir, [source_dir.resolve()])
    source_files = resolve_source_refs([str(path) for path in safe_files], repo_root)

    return ServiceInventory(
        service_id=service_id,
        routes=routes,
        symbols=symbols,
        source_files=source_files,
        ingested_at=datetime.now(timezone.utc).isoformat(),
    )
