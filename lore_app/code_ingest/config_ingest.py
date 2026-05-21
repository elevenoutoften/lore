from __future__ import annotations

import re
from pathlib import Path

from .schemas import ContainerSpec


def ingest_docker_compose(compose_path: str | Path) -> list[ContainerSpec]:
    """Parse docker-compose.yml or .yaml for service definitions."""

    path = Path(compose_path)
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8")
    specs: list[ContainerSpec] = []
    in_services = False
    current_service: ContainerSpec | None = None
    list_key: str | None = None

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "services:" or stripped.startswith("services:"):
            in_services = True
            current_service = None
            continue
        if in_services and not line.startswith(" ") and stripped:
            in_services = False
            current_service = None
            list_key = None
            continue
        if not in_services:
            continue

        service_match = re.match(r"^  ([A-Za-z0-9][\w-]*):\s*$", line)
        if service_match:
            current_service = ContainerSpec(name=service_match.group(1), kind="docker")
            specs.append(current_service)
            list_key = None
            continue
        if current_service is None:
            continue

        if re.match(r"^    [A-Za-z_][\w-]*:\s*$", line):
            list_key = stripped[:-1]
            continue
        if stripped.startswith("image:"):
            current_service.image = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            list_key = None
        elif stripped.startswith("ports:"):
            list_key = "ports"
        elif stripped.startswith("depends_on:"):
            list_key = "depends_on"
        elif stripped.startswith("- "):
            item = stripped[2:].strip().strip('"').strip("'")
            if list_key == "ports":
                current_service.ports.append(item)
            elif list_key == "depends_on":
                current_service.depends_on.append(item)

    return specs


def ingest_caddyfile(caddyfile_path: str | Path) -> list[ContainerSpec]:
    """Extract reverse_proxy targets from a Caddyfile."""

    path = Path(caddyfile_path)
    if not path.exists():
        return []

    content = path.read_text(encoding="utf-8")
    specs: list[ContainerSpec] = []
    for match in re.finditer(r"reverse_proxy\s+([\w.-]+)(?::(\d+))?", content):
        host = match.group(1)
        port = match.group(2) or ""
        specs.append(ContainerSpec(name=host, kind="docker", ports=[port] if port else []))
    return specs


def ingest_systemd_units(unit_dir: str | Path) -> list[ContainerSpec]:
    """Scan a directory for .service files and extract unit names."""

    unit_dir = Path(unit_dir)
    if not unit_dir.exists():
        return []

    return [
        ContainerSpec(name=unit_file.stem, kind="systemd", unit_file=str(unit_file))
        for unit_file in unit_dir.glob("*.service")
    ]

