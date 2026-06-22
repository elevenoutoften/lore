# Session-to-Daily Distillation

The distillation workflow consolidates session captures into daily notes,
providing an autonomous path from raw agent memory to canonical knowledge.

## Concepts

- **Session captures** are individual `inbox/` or `notes/` pages with `kind: capture`, created by agents during work sessions.
- **Daily notes** are consolidated pages under `dailies/YYYY-MM-DD` that combine all captures from a single date into one coherent document.
- **Promotion** marks a daily note as the canonical record for that date.

## Workflow

1. Agents create captures throughout the day via `lore_capture` or the `/api/memory/capture` endpoint.
2. At session end (or on schedule), call **distill** to consolidate the day's captures into a daily note.
3. Agents or scheduled automation verify confidence, sources, and conflicts.
4. **Promote** the daily note to mark it as canonical. Manual review is reserved
   for low-confidence, conflicting, or sensitive captures.

## API Endpoints

### POST /api/distill/daily

Distill captures into a daily note. Defaults to today's date.

```json
{
  "date": "2026-05-08",
  "actor": "lore-agent"
}
```

Both fields are optional. `date` defaults to today. Returns the daily note with all merged captures.

### GET /api/distill/daily/{date}

List all session captures for a specific date. Date must be in `YYYY-MM-DD` format.

### POST /api/distill/promote/{date}

Mark a daily note as canonical. Returns `{"page_id": "dailies/2026-05-08", "status": "promoted"}`.

### GET /api/distill/pending

List dates that have captures but no distilled daily note yet.

```json
{
  "pending_days": [
    {"date": "2026-05-07", "capture_count": 4},
    {"date": "2026-05-08", "capture_count": 12}
  ],
  "total": 2
}
```

## MCP Tools

### lore_distill_daily

Distill session captures for a date into a daily note.

```json
{
  "date": "2026-05-08",
  "actor": "lore-agent"
}
```

### lore_get_daily

List session captures for a specific date.

```json
{"date": "2026-05-08"}
```

### lore_promote_daily

Confirm a daily note is ready and mark it canonical.

```json
{"date": "2026-05-08"}
```

## Daily Note Structure

A distilled daily note contains:

- **Frontmatter**: title, kind (`daily-note`), visibility, status, summary, tags, `distilled_at` timestamp, actor, and sources listing all capture page IDs.
- **Body**: An introduction with capture count, followed by each capture as a section with its title, confidence/lane metadata, and full body text.

## Heartbeat Self-Audit Captures

The heartbeat self-audit adds a proactive maintenance loop alongside session captures. It scans for:

- **Stale pages** — pages past their `stale_after` date
- **Contradictions** — pages containing `CONTRADICTION:` markers
- **Low-confidence pages** — pages with low or unreviewed confidence
- **Expired facts** — pages past their `valid_until` date
- **Procedure issues** — procedures missing steps, trigger, or with body/frontmatter mismatch

### Creating Heartbeat Captures

Call `POST /api/heartbeat/captures` or the MCP tool `lore_heartbeat_audit` to generate one inbox capture draft per issue category. Each capture:

- Lives in `inbox/YYYY-MM-DD/` with prefix "Heartbeat audit:"
- Has `kind: capture`, `confidence: low`, `epistemic_status: hearsay`, `lane: ops`
- Includes `source_task: heartbeat-self-audit`
- Links to all affected pages via `related_pages`
- Is idempotent per category per day (repeat calls skip already-created categories)

### Heartbeat → Distill → Promote Flow

1. **Generate**: Call `lore_heartbeat_audit` or `POST /api/heartbeat/captures` at the start of each maintenance cycle.
2. **Review**: Inspect heartbeat captures in the inbox. They carry `confidence: low` and `hearsay` epistemic status — verify findings before promoting.
3. **Distill**: Call `lore_distill_daily` or `POST /api/distill/daily` to consolidate all captures (including heartbeat) into a daily note.
4. **Promote**: Once reviewed and verified, call `lore_promote_daily` or `POST /api/distill/promote/{date}` to mark the daily note as canonical.

The key difference from session captures: heartbeat captures are system-generated findings that need human or agent review before they become canonical knowledge. They should never be auto-promoted without verification.

## Implementation

The distillation logic lives in `lore_app/distillation.py`:

- `distill_daily(repo, payload)` — main entry point; finds captures, builds daily note, writes it.
- `get_daily_captures(repo, date)` — retrieves all captures matching a date.
- `get_pending_days(repo)` — finds dates with captures but no daily note.
- `promote_daily_note(repo, date)` — validates and returns the canonical page ID.
- `distill_session_to_daily(repo, captures, date, actor)` — builds the daily note content from captures.
