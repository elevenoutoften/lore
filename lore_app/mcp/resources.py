from __future__ import annotations

from typing import Any

from ..repository import InvalidPageId, normalize_page_id
from .dispatch import JsonRpcError


def list_resources(repo: Any) -> dict[str, Any]:
    pages = repo.list_pages()
    return {"resources": [resource_for_page(page.model_dump()) for page in pages]}


def read_resource(repo: Any, uri: str) -> dict[str, Any]:
    page_id = page_id_from_resource_uri(uri)
    page = repo.read_page(page_id)
    if page is None:
        raise JsonRpcError(-32004, "Lore page not found.", {"page_id": page_id})
    return {
        "contents": [
            {
                "uri": uri_for_page(page.id),
                "mimeType": "text/markdown",
                "text": page.content,
            }
        ]
    }


def list_resource_templates() -> dict[str, Any]:
    return {
        "resourceTemplates": [
            {
                "uriTemplate": "lore://pages/{page_id}",
                "name": "Lore page",
                "title": "Lore Page",
                "description": "Read a Markdown page from Lore.",
                "mimeType": "text/markdown",
            }
        ]
    }


def resource_for_page(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "uri": uri_for_page(str(page["id"])),
        "name": str(page["id"]),
        "title": str(page["title"]),
        "description": page.get("summary") or f"{page.get('kind', 'page')} page",
        "mimeType": "text/markdown",
        "size": page.get("size"),
        "annotations": {
            "audience": ["assistant"],
            "lastModified": page.get("updated_at"),
        },
    }


def uri_for_page(page_id: str) -> str:
    return f"lore://pages/{normalize_page_id(page_id)}"


def page_id_from_resource_uri(uri: str) -> str:
    prefix = "lore://pages/"
    if not uri.startswith(prefix):
        raise JsonRpcError(-32602, "Lore resource URI must start with lore://pages/.")
    try:
        return normalize_page_id(uri[len(prefix) :])
    except InvalidPageId as exc:
        raise JsonRpcError(-32602, str(exc)) from exc


def require_string(value: Any, name: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise JsonRpcError(-32602, f"Missing required field: {name}")
    return cleaned
