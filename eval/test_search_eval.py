"""Retrieval evaluation: score BM25 search against the eval corpus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lore_app.repository import LoreRepository
from lore_app.search_index import LoreSearchIndex

EVAL_CORPUS = Path(__file__).parent / "search_corpus.json"


def load_corpus() -> list[dict[str, Any]]:
    return json.loads(EVAL_CORPUS.read_text(encoding="utf-8"))


def score_search(idx: LoreSearchIndex, corpus: list[dict[str, Any]], kind: str | None = None) -> dict[str, Any]:
    """Run all queries and compute precision/recall metrics."""
    results: dict[str, Any] = {"queries": [], "top_hits": 0, "top_total": 0, "in_hits": 0, "in_total": 0}

    for entry in corpus:
        query = entry["query"]
        hits = idx.search(query, kind=kind, limit=10)
        hit_ids = [h["page_id"] for h in hits]

        top_ok = all(pid in hit_ids[:3] for pid in entry["expected_top"])
        in_ok = all(pid in hit_ids for pid in entry["expected_in"])

        results["queries"].append(
            {
                "query": query,
                "hit_ids": hit_ids,
                "expected_top": entry["expected_top"],
                "expected_in": entry["expected_in"],
                "top_ok": top_ok,
                "in_ok": in_ok,
            }
        )

        if entry["expected_top"]:
            results["top_total"] += 1
            if top_ok:
                results["top_hits"] += 1
        if entry["expected_in"]:
            results["in_total"] += 1
            if in_ok:
                results["in_hits"] += 1

    if results["top_total"]:
        results["top_precision"] = results["top_hits"] / results["top_total"]
    else:
        results["top_precision"] = 1.0

    if results["in_total"]:
        results["in_precision"] = results["in_hits"] / results["in_total"]
    else:
        results["in_precision"] = 1.0

    return results


def ensure_lore_eval_page(content_dir: Path) -> None:
    """Add the Lore page expected by the retrieval corpus."""
    path = content_dir / "services" / "lore.md"
    path.write_text(
        """---
title: Lore
kind: service
visibility: internal
status: active
tags: [lore, service]
---

# Lore

Knowledge base and durable project memory for ExampleProject services.
""",
        encoding="utf-8",
    )


def test_search_eval_corpus(content_dir: Path, tmp_path: Path) -> None:
    """BM25 search passes the retrieval eval corpus."""
    ensure_lore_eval_page(content_dir)
    repo = LoreRepository(content_dir)
    idx = LoreSearchIndex(tmp_path / "search.db")
    idx.rebuild(repo)

    corpus = load_corpus()
    scores = score_search(idx, corpus)

    print(f"\nSearch eval: top_precision={scores['top_precision']:.0%}, in_precision={scores['in_precision']:.0%}")
    for q in scores["queries"]:
        status = "OK" if q["top_ok"] and q["in_ok"] else "FAIL"
        print(f"  {status} {q['query']}: top={q['top_ok']}, in={q['in_ok']}, hits={q['hit_ids']}")

    # At least 60% of top-3 expectations should pass.
    assert scores["top_precision"] >= 0.6, f"Top-3 precision too low: {scores['top_precision']:.0%}"
    # At least 80% of "in results" expectations should pass.
    assert scores["in_precision"] >= 0.8, f"In-results precision too low: {scores['in_precision']:.0%}"

    idx.close()
