"""Run with: python -m services.lore.tests.benchmark_report"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
SERVICE_ROOT = ROOT / "services" / "lore"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from lore_app.config import LoreConfig  # noqa: E402
from lore_app.main import create_app  # noqa: E402
from lore_app.security import RateLimiter  # noqa: E402


def benchmark(client: TestClient, method: str, path: str, n: int = 100, **kwargs):
    times = []
    for _ in range(n):
        start = time.perf_counter()
        response = getattr(client, method.lower())(path, **kwargs)
        response.raise_for_status()
        times.append(time.perf_counter() - start)
    sorted_times = sorted(times)
    return {
        "n": n,
        "mean_ms": round(statistics.mean(times) * 1000, 2),
        "p50_ms": round(statistics.median(times) * 1000, 2),
        "p95_ms": round(sorted_times[int(n * 0.95)] * 1000, 2),
        "p99_ms": round(sorted_times[int(n * 0.99)] * 1000, 2),
        "max_ms": round(max(times) * 1000, 2),
    }


def markdown_page(index: int, body: str | None = None) -> str:
    return f"""---
title: Benchmark Page {index}
kind: benchmark
visibility: internal
summary: Benchmark fixture page {index}.
---

# Benchmark Page {index}

{body or f"This benchmark page documents ExampleProject service behavior number {index}."}
"""


def print_stats(name: str, stats: dict[str, float | int]) -> None:
    print(
        f"{name:<24} n={stats['n']:<4} "
        f"p50={stats['p50_ms']:>7}ms p95={stats['p95_ms']:>7}ms "
        f"p99={stats['p99_ms']:>7}ms max={stats['max_ms']:>7}ms"
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        content_dir = root / "pages"
        content_dir.mkdir()
        search_db = root / "search.db"
        config = LoreConfig()
        config.content_dir = content_dir
        config.search_db = search_db
        config.vector_db = root / "vectors.db"
        config.ledger_db = root / "ledger.db"
        config.api_keys_db = root / "api_keys.db"
        config.host = "127.0.0.1"
        app = create_app(config)
        app.state.write_rate_limiter = RateLimiter(max_requests=10_000, window_seconds=60)

        with TestClient(app) as client:
            client.put("/api/pages/bench/baseline", json={"content": markdown_page(0)}).raise_for_status()
            print("Lore API benchmark report")
            print("========================")
            print_stats("list pages", benchmark(client, "GET", "/api/pages", n=100))

            for index in range(50):
                client.put(f"/api/pages/bench/read-{index}", json={"content": markdown_page(index)}).raise_for_status()
            read_times = []
            for index in range(50):
                start = time.perf_counter()
                client.get(f"/api/pages/bench/read-{index}").raise_for_status()
                read_times.append(time.perf_counter() - start)
            print_stats("page reads", stats_from_times(read_times))

            write_times = []
            for index in range(50):
                start = time.perf_counter()
                client.put(f"/api/pages/bench/write-{index}", json={"content": markdown_page(index)}).raise_for_status()
                write_times.append(time.perf_counter() - start)
            print_stats("page writes", stats_from_times(write_times))

            for index in range(100):
                client.put(
                    f"/api/pages/bench/search-{index}",
                    json={"content": markdown_page(index, "latency throughput ExampleProject benchmark search")},
                ).raise_for_status()
            print_stats(
                "search",
                benchmark(client, "GET", "/api/search", n=50, params={"q": "latency throughput", "limit": 20}),
            )

            def read_page(_index: int):
                start = time.perf_counter()
                client.get("/api/pages/bench/baseline").raise_for_status()
                return time.perf_counter() - start

            with ThreadPoolExecutor(max_workers=10) as executor:
                concurrent_times = list(executor.map(read_page, range(200)))
            print_stats("concurrent reads", stats_from_times(concurrent_times))
    return 0


def stats_from_times(times: list[float]) -> dict[str, float | int]:
    sorted_times = sorted(times)
    n = len(times)
    return {
        "n": n,
        "mean_ms": round(statistics.mean(times) * 1000, 2),
        "p50_ms": round(statistics.median(times) * 1000, 2),
        "p95_ms": round(sorted_times[int(n * 0.95)] * 1000, 2),
        "p99_ms": round(sorted_times[int(n * 0.99)] * 1000, 2),
        "max_ms": round(max(times) * 1000, 2),
    }


if __name__ == "__main__":
    raise SystemExit(main())
