"""``lore`` — the agent memory CLI for Lore.

Six verbs over the memory backend, built on the resilient SDK clients
(``MemoryProvider`` for the ledger loop, ``LoreClient`` for pages):

    capture   recall   ack   search   read   write     (+ whoami)

Designed to be token-cheap for agents: invoke it from a shell, read terse text,
no tool schemas in context. Pass ``--json`` on any command for structured output.

Config (environment):
    LORE_BASE_URL   default https://lore.axis.love
    LORE_API_KEY    bearer token (a ``lore_`` key)

Examples:
    lore capture "Pixl renders text as garbage on Illustrious XL" --lane project
    lore recall "pixl text rendering" --limit 5
    lore ack 1a2b3c4d
    lore search "memory backend"
    lore read services/lore
    lore write runbooks/deploy --file deploy.md -m "update steps"
    lore whoami
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .client import LoreClient
from .exceptions import LoreError
from .memory_provider import MemoryProvider, MemoryProviderError

DEFAULT_BASE_URL = "https://lore.axis.love"


def _base_url() -> str:
    return (os.environ.get("LORE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _api_key() -> str:
    return (os.environ.get("LORE_API_KEY") or "").strip()


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _dump(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ----------------------------------------------------------------------------
# Commands — each takes (args, mp, client) and returns an exit code.
# ----------------------------------------------------------------------------


def cmd_capture(args: argparse.Namespace, mp: MemoryProvider, client: LoreClient) -> int:
    metadata: dict[str, Any] = {}
    if args.confidence:
        metadata["confidence"] = args.confidence
    if args.source_task:
        metadata["source_task"] = args.source_task
    capture_id = mp.capture(
        memory_text=args.text,
        lane=args.lane,
        agent_name=args.agent,
        metadata=metadata or None,
    )
    if args.json:
        _dump({"capture_id": capture_id})
    else:
        print(f"captured {capture_id}")
    return 0


def cmd_recall(args: argparse.Namespace, mp: MemoryProvider, client: LoreClient) -> int:
    envelope = mp.recall_response(
        args.query,
        limit=args.limit,
        lane=args.lane,
        subject=args.subject,
        min_strength=args.min_strength,
        cross_actor=args.cross_actor,
    )
    if args.json:
        _dump(envelope)
        return 0
    claims = envelope.get("claims") or []
    for claim in claims:
        score = claim.get("recall_score", 0.0)
        subject = claim.get("subject", "")
        predicate = claim.get("predicate", "")
        obj = claim.get("object", "")
        cid = claim.get("candidate_id", "")
        print(f"{score:5.2f}  {subject} {predicate} {obj}  [{cid}]")
    if not claims:
        # Self-diagnosing: surface why it was empty (pending consolidation,
        # scoped to another actor, or genuinely nothing) on stderr.
        _err(envelope.get("hint") or "No matching memory.")
    return 0


def cmd_ack(args: argparse.Namespace, mp: MemoryProvider, client: LoreClient) -> int:
    result = mp.acknowledge_recall(args.candidate_ids)
    if args.json:
        _dump(result)
    else:
        print(f"acked {result.get('acknowledged_count', 0)}")
    return 0


def cmd_search(args: argparse.Namespace, mp: MemoryProvider, client: LoreClient) -> int:
    result = client.search(args.query, limit=args.limit)
    hits = result.get("hits", result.get("results", [])) if isinstance(result, dict) else result
    if args.json:
        _dump(result)
        return 0
    for hit in hits or []:
        page = hit.get("page", hit) if isinstance(hit, dict) else {}
        page_id = page.get("id") or (hit.get("id") if isinstance(hit, dict) else "")
        title = page.get("title") or ""
        print(f"{page_id}  {title}".rstrip())
    if not hits:
        _err("No results.")
    return 0


def cmd_read(args: argparse.Namespace, mp: MemoryProvider, client: LoreClient) -> int:
    page = client.get_page(args.page_id)
    if args.json:
        _dump(page)
        return 0
    content = page.get("content") if isinstance(page, dict) else None
    print(content if content is not None else json.dumps(page, ensure_ascii=False, indent=2))
    return 0


def cmd_write(args: argparse.Namespace, mp: MemoryProvider, client: LoreClient) -> int:
    if args.file:
        with open(args.file, encoding="utf-8") as handle:
            content = handle.read()
    else:
        content = sys.stdin.read()
    result = client.upsert_page(args.page_id, content, commit_message=args.message)
    page_id = result.get("id", args.page_id) if isinstance(result, dict) else args.page_id
    if args.json:
        _dump(result)
    else:
        print(f"wrote {page_id}")
    return 0


def cmd_whoami(args: argparse.Namespace, mp: MemoryProvider, client: LoreClient) -> int:
    result = client.whoami()
    if args.json:
        _dump(result)
        return 0
    actor = result.get("actor") if isinstance(result, dict) else None
    role = result.get("role") if isinstance(result, dict) else None
    auth_mode = result.get("auth_mode") if isinstance(result, dict) else None
    print(f"actor={actor}  role={role}  auth_mode={auth_mode}  base_url={client.base_url}")
    return 0


# ----------------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="emit raw JSON instead of text")

    parser = argparse.ArgumentParser(
        prog="lore",
        description="Agent memory CLI for Lore: capture/recall/ack/search/read/write.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("capture", parents=[common], help="write a memory (capture)")
    sp.add_argument("text", help="the observation/insight to remember")
    sp.add_argument("--lane", help="project | procedural | ops | companion | draft")
    sp.add_argument("--source-task", dest="source_task", help="originating task id, e.g. flow_000123")
    sp.add_argument("--confidence", choices=["high", "medium", "low"])
    sp.add_argument("--agent", help="agent name for the notes namespace (advisory)")
    sp.set_defaults(func=cmd_capture)

    sp = sub.add_parser("recall", parents=[common], help="recall ranked memory")
    sp.add_argument("query", nargs="?", help="natural-language query (omit to list by score)")
    sp.add_argument("--limit", type=int, default=10)
    sp.add_argument("--lane")
    sp.add_argument("--subject", help="filter to claims about an exact subject")
    sp.add_argument("--min-strength", dest="min_strength", type=float, default=0.0)
    sp.add_argument(
        "--cross-actor",
        dest="cross_actor",
        action="store_true",
        help="admin only: recall across all actors instead of your own",
    )
    sp.set_defaults(func=cmd_recall)

    sp = sub.add_parser("ack", parents=[common], help="acknowledge recalled claims (boost salience)")
    sp.add_argument("candidate_ids", nargs="+", help="candidate_id values from a recall")
    sp.set_defaults(func=cmd_ack)

    sp = sub.add_parser("search", parents=[common], help="lexical page search")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("read", parents=[common], help="read a page as Markdown")
    sp.add_argument("page_id")
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("write", parents=[common], help="create/replace a page from --file or stdin")
    sp.add_argument("page_id")
    sp.add_argument("--file", help="path to a Markdown file (otherwise reads stdin)")
    sp.add_argument("--message", "-m", help="optional commit message")
    sp.set_defaults(func=cmd_write)

    sp = sub.add_parser("whoami", parents=[common], help="show the resolved actor/role")
    sp.set_defaults(func=cmd_whoami)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base_url = _base_url()
    api_key = _api_key()
    mp = MemoryProvider(base_url=base_url, api_key=api_key)
    client = LoreClient(base_url=base_url, auth_token=api_key or None)
    try:
        return int(args.func(args, mp, client))
    except (LoreError, MemoryProviderError) as exc:
        _err(f"lore: {exc}")
        return 1
    except FileNotFoundError as exc:
        _err(f"lore: {exc}")
        return 1
    except BrokenPipeError:  # piped into head etc.
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
