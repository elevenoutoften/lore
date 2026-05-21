from __future__ import annotations

from pydantic import BaseModel, Field


class RouteEntry(BaseModel):
    """A single HTTP route from a service."""

    method: str
    path: str
    function_name: str | None = None
    file_path: str | None = None
    line_number: int | None = None


class ContainerSpec(BaseModel):
    """A Docker container or systemd unit."""

    name: str
    kind: str
    image: str | None = None
    ports: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    unit_file: str | None = None


class SymbolEntry(BaseModel):
    """A code symbol (function, class, constant)."""

    name: str
    kind: str
    file_path: str
    line_number: int | None = None
    docstring: str | None = None
    exports: list[str] = Field(default_factory=list)


class SourceReference(BaseModel):
    """A structured reference to a source file."""

    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    language: str | None = None
    symbol: str | None = None


class ServiceInventory(BaseModel):
    """Complete code inventory for a service page."""

    service_id: str
    routes: list[RouteEntry] = Field(default_factory=list)
    containers: list[ContainerSpec] = Field(default_factory=list)
    symbols: list[SymbolEntry] = Field(default_factory=list)
    source_files: list[SourceReference] = Field(default_factory=list)
    ingested_at: str | None = None

