# Consolidation

Lore consolidation turns draft capture pages into extracted candidates, patch plans, and safe page updates.

## One-pass CLI

Run a dry pass with:

```bash
lore-admin consolidate
```

Dry run is the default. It extracts from the selected captures and reports the patch plans that would be generated, but it does not persist extraction candidates, patch plans, run records, or worker traces.

Run and auto-apply safe plans with:

```bash
lore-admin consolidate --apply
```

Useful flags:

- `--max-auto-apply N`: maximum number of safe plans to apply in this pass. When omitted with `--apply`, Lore defaults to 5.
- `--batch-size N`: maximum number of draft captures to process.
- `--force-reextract`: clear previous extraction state before an apply run so already-extracted captures can be processed again.

## Schedule-friendly Runner

Use `scripts/run-consolidation.sh` from cron or systemd when a scheduler should call the Lore API instead of running the CLI in-process.

The runner reads these environment variables:

- `CONSOLIDATION_DRY_RUN`: `true` or `false`; defaults to `true`.
- `CONSOLIDATION_BATCH_SIZE`: capture batch size; defaults to `20`.
- `CONSOLIDATION_MAX_AUTO_APPLY`: maximum safe plans to auto-apply; defaults to `3`.
- `LORE_URL`: base URL for the Lore server; defaults to `http://127.0.0.1:8210`.
- `LORE_BEARER_TOKEN`: optional bearer token for authenticated consolidation endpoints.

Example:

```bash
CONSOLIDATION_DRY_RUN=false \
CONSOLIDATION_BATCH_SIZE=20 \
CONSOLIDATION_MAX_AUTO_APPLY=3 \
LORE_URL=http://127.0.0.1:8210 \
scripts/run-consolidation.sh
```

## Status

Show the current consolidation status with:

```bash
lore-admin status
```

The output includes the last run timestamp, pending captures, patch plans by status, total generated plans, auto-applied plans, plans requiring review, errors from the last run, and stuck runs.

Example:

```json
{
  "last_run": "2026-05-22T12:00:00+00:00",
  "pending_captures": 2,
  "plans_by_status": {
    "draft": 0,
    "pending": 1,
    "needs_manual_review": 0,
    "applied": 3,
    "rejected": 0
  },
  "generated_plans": 4,
  "auto_applied": 3,
  "review_required": 1,
  "errors": [],
  "stuck_runs": 0
}
```

## Dry-run Semantics

`lore-admin consolidate` and `lore-admin consolidate --dry-run` are non-destructive. They run extraction and planning in memory or temporary storage and leave the durable ledger unchanged. This makes dry runs safe to repeat.

`lore-admin consolidate --apply` persists extraction state, stores patch plans, records the consolidation run and worker trace, and auto-applies safe plans up to `--max-auto-apply`.

If an apply run has already processed captures, a later apply run skips those captures unless `--force-reextract` is used. A dry run followed by an apply run does not require `--force-reextract`, because dry runs do not mark captures as extracted.
