from __future__ import annotations

import argparse
import json
import logging
import math
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lore_app.ledger import LedgerDB  # noqa: E402
from lore_app.repository import LoreRepository  # noqa: E402
from lore_app.schemas import ExtractedClaim, ExtractionResult  # noqa: E402
from lore_app.search_index import LoreSearchIndex  # noqa: E402


@dataclass(frozen=True)
class WriteConcurrencyMetrics:
    requested_writes: int
    max_workers: int
    success_count: int
    retry_count: int
    failure_count: int
    ledger_candidates: int
    search_hits: int
    markdown_pages: int
    p50_ms: float
    p99_ms: float
    max_ms: float
    elapsed_ms: float
    failures: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RetryCountingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.retry_count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if "SQLite database locked, retrying" in record.getMessage():
            self.retry_count += 1


def run_write_concurrency_harness(
    root: Path,
    *,
    writes: int = 40,
    max_workers: int = 10,
) -> WriteConcurrencyMetrics:
    """Drive concurrent ledger, markdown, and search-index writes."""
    root.mkdir(parents=True, exist_ok=True)
    ledger = LedgerDB(root / "ledger.db")
    ledger.initialize()
    search = LoreSearchIndex(root / "search.db")
    repo = LoreRepository(root / "pages", search_index=search)
    repo.ensure_root()

    retry_handler = RetryCountingHandler()
    retry_logger = logging.getLogger("lore_app.db_utils")
    retry_logger.addHandler(retry_handler)

    started = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_write_cycle, ledger, repo, search, i) for i in range(writes)]
            results = [future.result() for future in as_completed(futures)]

        latencies = [elapsed for ok, elapsed, _failure in results if ok]
        failures = [failure for ok, _elapsed, failure in results if not ok and failure is not None]
        ledger_candidates = len(ledger.get_candidates(candidate_type="claim", limit=max(writes * 2, 100)))
        search_hits = len(search.search("concurrencyneedle", limit=max(writes * 2, 100)))
        markdown_pages = len(list((root / "pages" / "bench").glob("write-*.md")))
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return WriteConcurrencyMetrics(
            requested_writes=writes,
            max_workers=max_workers,
            success_count=len(latencies),
            retry_count=retry_handler.retry_count,
            failure_count=len(failures),
            ledger_candidates=ledger_candidates,
            search_hits=search_hits,
            markdown_pages=markdown_pages,
            p50_ms=_p50_ms(latencies),
            p99_ms=_p99_ms(latencies),
            max_ms=round(max(latencies) * 1000, 2) if latencies else 0.0,
            elapsed_ms=elapsed_ms,
            failures=failures,
        )
    finally:
        retry_logger.removeHandler(retry_handler)
        ledger.close()
        search.close()


def _write_cycle(
    ledger: LedgerDB,
    repo: LoreRepository,
    search: LoreSearchIndex,
    index: int,
) -> tuple[bool, float, str | None]:
    started = time.perf_counter()
    try:
        ledger.store_extraction_result(_extraction_result(index))
        detail = repo.upsert_page(f"bench/write-{index}", _markdown_page(index))
        search.upsert_page_from_detail(detail)
    except Exception as exc:  # pragma: no cover - surfaced in metrics for benchmark runs.
        return False, time.perf_counter() - started, f"{type(exc).__name__}: {exc}"
    return True, time.perf_counter() - started, None


def _extraction_result(index: int) -> ExtractionResult:
    return ExtractionResult(
        batch_id=f"bench-concurrent-{index}",
        processed_at="2026-06-27T00:00:00+00:00",
        source_capture_ids=[f"inbox/concurrent-{index}"],
        claims=[
            ExtractedClaim(
                subject=f"services/lore-concurrency-{index}",
                predicate="records",
                object=f"concurrencyneedle write result {index}",
                confidence="high",
                source_page_ids=[f"bench/write-{index}"],
            )
        ],
    )


def _markdown_page(index: int) -> str:
    return f"""---
title: Concurrency Write {index}
kind: benchmark
visibility: internal
summary: Concurrent write benchmark fixture {index}.
---

# Concurrency Write {index}

concurrencyneedle ledger and search write fixture {index}.
"""


def _p50_ms(latencies: list[float]) -> float:
    if not latencies:
        return 0.0
    return round(statistics.median(latencies) * 1000, 2)


def _p99_ms(latencies: list[float]) -> float:
    if not latencies:
        return 0.0
    ordered = sorted(latencies)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * 0.99) - 1))
    return round(ordered[index] * 1000, 2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Lore's concurrent write benchmark.")
    parser.add_argument("--writes", type=int, default=200, help="Total ledger/search write cycles to run.")
    parser.add_argument("--max-workers", type=int, default=10, help="ThreadPoolExecutor worker count.")
    parser.add_argument("--json", action="store_true", help="Print raw JSON metrics.")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as temp_dir:
        metrics = run_write_concurrency_harness(
            Path(temp_dir),
            writes=max(1, args.writes),
            max_workers=max(1, args.max_workers),
        )

    if args.json:
        print(json.dumps(metrics.to_dict(), indent=2, sort_keys=True))
        return 0 if metrics.failure_count == 0 else 1

    print("Lore concurrent write benchmark")
    print("===============================")
    print(f"writes={metrics.requested_writes} max_workers={metrics.max_workers}")
    print(
        f"success={metrics.success_count} retries={metrics.retry_count} failures={metrics.failure_count}"
    )
    print(
        f"persisted ledger={metrics.ledger_candidates} "
        f"search={metrics.search_hits} markdown={metrics.markdown_pages}"
    )
    print(
        f"latency p50={metrics.p50_ms}ms p99={metrics.p99_ms}ms "
        f"max={metrics.max_ms}ms elapsed={metrics.elapsed_ms}ms"
    )
    if metrics.failures:
        print("failures:")
        for failure in metrics.failures:
            print(f"- {failure}")
    return 0 if metrics.failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
