"""Single source of truth for the lane and role allowlists.

Centralised so the API, MCP tool schemas, and validation paths cannot drift.
Tuples define the canonical display order; frozensets are for membership tests.
"""

from __future__ import annotations

# Memory lanes, in canonical display order.
LANES: tuple[str, ...] = ("project", "procedural", "ops", "companion", "draft")
ALLOWED_LANES: frozenset[str] = frozenset(LANES)

# API-key / browser-session roles, in canonical order.
API_KEY_ROLES: tuple[str, ...] = ("admin", "writer", "reader")
VALID_API_KEY_ROLES: frozenset[str] = frozenset(API_KEY_ROLES)
