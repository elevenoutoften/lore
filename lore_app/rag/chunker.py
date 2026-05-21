"""Chunk Lore pages into searchable segments for RAG."""
from __future__ import annotations

import re
from typing import Any


def chunk_page(
    page_id: str,
    content: str,
    body: str,
    chunk_size: int = 500,
    overlap: int = 100,
) -> list[dict[str, Any]]:
    """Split a page into chunks of approximately chunk_size characters with overlap."""
    text = body or content
    sections = _split_by_headings(text)

    chunks: list[dict[str, Any]] = []
    current_text = ""
    index = 0

    for section in sections:
        if len(current_text) + len(section) > chunk_size and current_text:
            chunks.append(_make_chunk(page_id, index, current_text.strip()))
            index += 1
            current_text = f"{current_text[-overlap:]}\n{section}" if overlap else section
        else:
            current_text = f"{current_text}\n{section}".strip() if current_text else section

    if current_text.strip():
        chunks.append(_make_chunk(page_id, index, current_text.strip()))

    return chunks


def _split_by_headings(text: str) -> list[str]:
    """Split text on markdown headings, keeping the heading with its section."""
    parts = re.split(r"(?=\n#{1,6}\s)", text)
    return [part.strip() for part in parts if part.strip()]


def _make_chunk(page_id: str, index: int, content: str) -> dict[str, Any]:
    return {
        "chunk_id": f"{page_id}#{index}",
        "page_id": page_id,
        "chunk_index": index,
        "content": content,
    }
