"""Standalone Lore MCP server using stdio transport."""

from __future__ import annotations

import json
import sys

from .config import LoreConfig
from .ledger import LedgerDB
from .link_graph import LinkGraphCache
from .mcp import PROJECT_NAME, PROTOCOL_VERSION, TOOLS
from .rag.vector_store import VectorStore
from .repository import LoreRepository
from .search_index import LoreSearchIndex


def main() -> int:
    config = LoreConfig()
    config.content_dir.mkdir(parents=True, exist_ok=True)

    repo = LoreRepository(str(config.content_dir))
    search_idx = LoreSearchIndex(str(config.search_db))
    vector_store = VectorStore(str(config.vector_db))
    ledger_db = LedgerDB(config.ledger_db)
    ledger_db.initialize()
    graph_cache = LinkGraphCache()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = message.get("method", "")
        msg_id = message.get("id")
        params = message.get("params", {})

        response: dict[str, object] = {"jsonrpc": "2.0", "id": msg_id}

        if method == "initialize":
            response["result"] = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": PROJECT_NAME, "version": "0.3.0b1"},
            }
        elif method == "tools/list":
            response["result"] = {"tools": TOOLS}
        elif method == "tools/call":
            from .mcp import call_tool

            try:
                response["result"] = call_tool(repo, params, search_idx, graph_cache, vector_store, ledger_db=ledger_db)
            except Exception as exc:
                response["error"] = {"code": -32603, "message": str(exc)}
        else:
            response["error"] = {"code": -32601, "message": f"Unsupported method: {method}"}

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
