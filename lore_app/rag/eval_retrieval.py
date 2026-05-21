"""Simple retrieval evaluation using precision@k and recall@k."""
from __future__ import annotations

from typing import Any


def evaluate_retrieval(
    queries: list[dict[str, Any]],
    retrieve_fn,
    k: int = 5,
) -> dict[str, Any]:
    """Evaluate retrieval quality over a set of test queries."""
    per_query: list[dict[str, Any]] = []

    for query_case in queries:
        query_text = query_case["query"]
        relevant = set(query_case["relevant_page_ids"])
        result = retrieve_fn(query_text, limit=k)
        retrieved = {item["page_id"] for item in result.get("results", [])}

        hits = relevant & retrieved
        precision = len(hits) / len(retrieved) if retrieved else 0.0
        recall = len(hits) / len(relevant) if relevant else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0

        per_query.append(
            {
                "query": query_text,
                "relevant": sorted(relevant),
                "retrieved": sorted(retrieved),
                "precision": precision,
                "recall": recall,
                "f1": f1,
            }
        )

    if not per_query:
        return {"mean_precision": 0.0, "mean_recall": 0.0, "mean_f1": 0.0, "per_query": []}

    return {
        "mean_precision": sum(item["precision"] for item in per_query) / len(per_query),
        "mean_recall": sum(item["recall"] for item in per_query) / len(per_query),
        "mean_f1": sum(item["f1"] for item in per_query) / len(per_query),
        "per_query": per_query,
    }
