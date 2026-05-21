"""Performance benchmarks for Lore API - run with pytest -v --tb=short."""
from __future__ import annotations

import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from lore_app.security import RateLimiter


def benchmark(client, method, path, n=100, **kwargs):
    """Run n requests and return timing stats."""
    times = []
    responses = []
    for _ in range(n):
        start = time.perf_counter()
        response = getattr(client, method.lower())(path, **kwargs)
        elapsed = time.perf_counter() - start
        times.append(elapsed)
        responses.append(response)
    sorted_times = sorted(times)
    return {
        "n": n,
        "mean_ms": round(statistics.mean(times) * 1000, 2),
        "p50_ms": round(statistics.median(times) * 1000, 2),
        "p95_ms": round(sorted_times[int(n * 0.95)] * 1000, 2),
        "p99_ms": round(sorted_times[int(n * 0.99)] * 1000, 2),
        "max_ms": round(max(times) * 1000, 2),
        "responses": responses,
    }


def markdown_page(index: int, *, title: str | None = None, body: str | None = None) -> str:
    page_title = title or f"Benchmark Page {index}"
    page_body = body or f"This benchmark page documents ExampleProject service behavior number {index}."
    return f"""---
title: {page_title}
kind: benchmark
visibility: internal
summary: Benchmark fixture page {index}.
---

# {page_title}

{page_body}
"""


def unthrottle_writes(client) -> None:
    client.app.state.write_rate_limiter = RateLimiter(max_requests=10_000, window_seconds=60)


@pytest.mark.benchmark
def test_list_pages_performance(client):
    stats = benchmark(client, "GET", "/api/pages", n=100)
    assert all(response.status_code == 200 for response in stats["responses"])
    assert stats["p50_ms"] < 50


@pytest.mark.benchmark
def test_page_read_performance(client):
    unthrottle_writes(client)
    for index in range(50):
        response = client.put(f"/api/pages/bench/read-{index}", json={"content": markdown_page(index)})
        assert response.status_code == 200

    times = []
    for index in range(50):
        start = time.perf_counter()
        response = client.get(f"/api/pages/bench/read-{index}")
        elapsed = time.perf_counter() - start
        assert response.status_code == 200
        times.append(elapsed)

    assert round(statistics.median(times) * 1000, 2) < 30


@pytest.mark.benchmark
def test_page_write_performance(client):
    unthrottle_writes(client)
    times = []
    for index in range(50):
        start = time.perf_counter()
        response = client.put(f"/api/pages/bench/write-{index}", json={"content": markdown_page(index)})
        elapsed = time.perf_counter() - start
        assert response.status_code == 200
        times.append(elapsed)

    assert round(statistics.median(times) * 1000, 2) < 100


@pytest.mark.benchmark
def test_search_performance(client):
    unthrottle_writes(client)
    for index in range(100):
        content = markdown_page(index, body=f"Search benchmark page {index} mentions latency throughput ExampleProject.")
        response = client.put(f"/api/pages/bench/search-{index}", json={"content": content})
        assert response.status_code == 200

    stats = benchmark(client, "GET", "/api/search", n=50, params={"q": "latency throughput", "limit": 20})
    assert all(response.status_code == 200 for response in stats["responses"])
    assert stats["p50_ms"] < 100


@pytest.mark.benchmark
def test_concurrent_reads(client):
    unthrottle_writes(client)
    response = client.put("/api/pages/bench/concurrent", json={"content": markdown_page(0)})
    assert response.status_code == 200

    def read_page(_index: int):
        start = time.perf_counter()
        read_response = client.get("/api/pages/bench/concurrent")
        return read_response.status_code, time.perf_counter() - start

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(read_page, range(200)))

    assert all(status_code == 200 for status_code, _elapsed in results)
    assert round(statistics.median(elapsed for _status_code, elapsed in results) * 1000, 2) < 50
