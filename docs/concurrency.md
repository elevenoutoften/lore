# Concurrency Model

Lore is a single-process, Markdown-backed service. Its concurrency model is
optimized for multiple in-process agent requests writing to the same local
vault, not for many independent service processes writing the same SQLite files.

## Current Model

- Markdown pages are the source of truth. SQLite stores derived indexes,
  claim-ledger state, settings, API keys, and vector/search support data.
- SQLite databases run in WAL mode with `busy_timeout = 5000`, so readers and a
  single writer can coexist and transient lock waits get a bounded window.
- Ledger writes use per-thread SQLite connections. Each thread opens its own
  connection, and all opened connections are tracked so shutdown can close them.
- Ledger and search writes are wrapped with `retry_on_locked`, which retries
  `sqlite3.OperationalError: database is locked` three times with backoff.
- The ledger uses an in-process `RLock` around schema initialization, migrations,
  and write transactions. Search indexing uses an in-process `RLock` around
  rebuild, upsert, and removal operations.
- The service has no cross-process write lock. Running two Lore processes
  against the same content directory and database files is outside the supported
  write model.

## Measured Safe Writer Ceiling

The repeatable harness is:

```bash
python scripts/write_concurrency_benchmark.py --writes 200 --max-workers 10
```

The CI-safe regression calls the same harness with 40 write cycles and
`ThreadPoolExecutor(max_workers=10)`. Each write cycle stores one ledger
extraction result, writes one Markdown page, and upserts the search index. The
test asserts success/failure counts and persisted ledger, search, and Markdown
counts instead of asserting a brittle latency threshold.

Current local measurements on June 27, 2026:

| Writes | Workers | Success | Retries | Failures | Ledger rows | Search hits | Markdown pages | p50 | p99 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 40 | 10 | 40 | 0 | 0 | 40 | 40 | 40 | 17.0 ms | 166.03 ms |
| 200 | 10 | 200 | 0 | 0 | 200 | 200 | 200 | 16.77 ms | 162.36 ms |

This makes 10 in-process concurrent writers the documented safe ceiling for the
current SQLite-backed local service, provided a single Lore process owns the
vault and database files.

## Upgrade Triggers

Keep SQLite as the default while the harness reports no data loss and acceptable
p99 latency at 10 in-process writers.

Use these findings as the go/no-go trigger for the storage/index backlog:

- Kuzu or a maintained fork (`flow_000913`) is only worth a spike when the
  in-memory graph rebuild becomes too expensive and the concurrency harness still
  shows the SQLite/write model is not the bottleneck.
- PostgreSQL (`flow_000914`) is only worth a design spike when Lore needs hosted
  multi-tenant or multi-process concurrent writers. That is a product-model
  change, not a drop-in optimization, because Markdown remains source of truth
  and FTS/vector/ledger behavior must be redesigned together.
