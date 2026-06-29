#!/usr/bin/env python
"""Nyx / Hermes agentic smoke test for Lore.

Exercises the agent-facing memory loop the way an agent (Nyx, via her Hermes
harness) would actually use it, and reports PASS/FAIL per step.

What it checks:
  1. capture            POST /api/memory/capture        (MemoryProvider.capture)
  2. consolidate        POST /api/consolidation/run     (capture -> ledger claims)
  3. recall (default)   GET  /api/memory/recall         (MemoryProvider.recall)
  4. recall (x-actor)   GET  /api/memory/recall?cross_actor=true   [diagnostic]
  5. ack                POST /api/memory/recall/ack     (MemoryProvider.acknowledge_recall)
  6. search / rag       supplementary read surfaces
  7. legacy path        POST /api/capture + /api/search (what the Hermes plugin wires today)

Run (PowerShell):
    $env:PYTHONUTF8=1; $env:LORE_BASE_URL="https://lore.axis.love"
    python testing/nyx-agentic-smoke.py
Run (bash):
    PYTHONUTF8=1 LORE_BASE_URL=https://lore.axis.love python testing/nyx-agentic-smoke.py

Env:
    LORE_BASE_URL   default https://lore.axis.love
    LORE_API_KEY    required (a lore_ key; admin lets the cross_actor diagnostic run)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

# --- wire up the bundled Python SDK ----------------------------------------
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "sdk" / "python"))
from lore_sdk import LoreClient, MemoryProvider  # noqa: E402  (imported after the sys.path tweak above)

BASE = os.environ.get("LORE_BASE_URL", "https://lore.axis.love").rstrip("/")
KEY = os.environ.get("LORE_API_KEY", "").strip()
MARK = f"nyx-smoke-{int(time.time())}"

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def http(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Minimal stdlib HTTP for the endpoints the SDK does not wrap."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {KEY}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        return e.code, {"error": e.read().decode(errors="replace")[:300]}


def main() -> int:
    if not KEY:
        print("LORE_API_KEY is not set. Aborting.")
        return 2

    print(f"== Nyx agentic smoke test ==\n   base={BASE}\n   marker={MARK}\n")
    mem = MemoryProvider(base_url=BASE, api_key=KEY)

    # 1. capture -------------------------------------------------------------
    text = (
        f"{MARK}: Nyx agentic smoke test ran against Lore on this date; "
        f"the capture->consolidate->recall->ack loop is under test."
    )
    try:
        cap_id = mem.capture(memory_text=text, lane="ops", metadata={"confidence": "high", "source_task": MARK})
        record("1. capture", bool(cap_id), str(cap_id))
    except Exception as e:
        record("1. capture", False, repr(e))
        return _summary()

    # 2. consolidate (auto-consolidation usually fires; force it to be sure) --
    status, run = http("POST", "/api/consolidation/run", {"dry_run": False})
    applied = run.get("auto_applied") if isinstance(run, dict) else None
    record(
        "2. consolidate",
        status == 200,
        f"http={status} captures_processed={run.get('captures_processed')} "
        f"candidates={run.get('candidates_extracted')} applied={applied} "
        f"dry_run={run.get('dry_run')}",
    )

    # 3. recall (DEFAULT scoping — the documented 'just works' loop) ----------
    #    NOTE: MemoryProvider.recall() returns ONLY the claims list, dropping the
    #    raw API's self-diagnosing hint/pending_captures — an SDK ergonomic gap.
    default_claims: list = []
    for _ in range(5):
        default_claims = mem.recall(query=MARK, limit=5)
        if default_claims:
            break
        time.sleep(2)
    found_default = len(default_claims)
    record(
        "3. recall (default scope)",
        found_default > 0,
        f"count={found_default}" + ("" if found_default else "  <-- captured memory NOT visible under default scope"),
    )

    # 4. cross-actor diagnostic (admin only) ---------------------------------
    claims = mem.recall(query=MARK, limit=5, cross_actor=True)
    found_x = len(claims)
    actors = sorted({str(c.get("actor")) for c in claims})
    if found_default == 0 and found_x > 0:
        record(
            "4. cross_actor diagnostic",
            True,
            f"FOUND with cross_actor=true (count={found_x}, claim actors={actors}). "
            f"=> Default actor-scoped recall is MISSING freshly captured memory: "
            f"the caller's actor != the actor stamped on the extracted claims. "
            f"This breaks the single-agent capture->recall loop. (See review.)",
        )
    elif found_default > 0:
        record("4. cross_actor diagnostic", True, "n/a — default recall already worked")
    else:
        record("4. cross_actor diagnostic", found_x > 0, f"cross_actor count={found_x} actors={actors}")

    # 5. ack -----------------------------------------------------------------
    ids = [c.get("candidate_id") for c in (claims or default_claims) if c.get("candidate_id")]
    if ids:
        try:
            ack = mem.acknowledge_recall(ids[:3])
            record("5. ack recall", True, f"acked {len(ids[:3])} -> {json.dumps(ack)[:120]}")
        except Exception as e:
            record("5. ack recall", False, repr(e))
    else:
        record("5. ack recall", False, "no candidate_ids to ack (recall returned nothing)")

    # 6. supplementary reads -------------------------------------------------
    client = LoreClient(base_url=BASE, auth_token=KEY)
    try:
        s = client.search("memory backend", limit=3)
        hits = s.get("hits", s.get("results", [])) if isinstance(s, dict) else s
        record("6a. search", bool(hits), f"hits={len(hits) if hits else 0}")
    except Exception as e:
        record("6a. search", False, repr(e))
    status, rag = http(
        "POST", "/api/rag/retrieve-expanded", {"query": "how does agent memory recall ranking work", "limit": 3}
    )
    record(
        "6b. rag retrieve-expanded",
        status == 200,
        f"http={status} results={len(rag.get('results', [])) if isinstance(rag, dict) else '?'}",
    )

    # 7. legacy path the Hermes plugin actually wires today ------------------
    #    sdk/hermes LoreMemoryProvider -> LoreClient.create_capture (POST /api/capture)
    #    + LoreClient.search() as 'recall'. NOT the ledger loop above.
    try:
        legacy = client.create_capture(
            title=f"{MARK} legacy",
            body=f"{MARK}: legacy capture path used by the current Hermes plugin.",
            source="testing/nyx-agentic-smoke.py",
            confidence="medium",
        )
        record(
            "7. legacy capture (Hermes plugin path)",
            isinstance(legacy, dict),
            "NOTE: the Hermes plugin uses POST /api/capture + page search, "
            "NOT /api/memory/recall — it does not use the new ledger loop.",
        )
    except Exception as e:
        record("7. legacy capture (Hermes plugin path)", False, repr(e))

    return _summary()


def _summary() -> int:
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n== summary: {passed}/{len(results)} passed ==")
    failed = [n for n, ok, _ in results if not ok]
    if failed:
        print("   failed: " + ", ".join(failed))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
